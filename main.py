from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Literal, Optional
from beanie import Document, init_beanie, PydanticObjectId
from pymongo import AsyncMongoClient
import pandas as pd
import io
import os
import httpx

app = FastAPI(title="Transaction Service")

# URL Service Profiling (Diambil dari Env Var)
PROFILING_SERVICE_URL = os.getenv("PROFILING_SERVICE_URL", "http://localhost:8001")

# MongoDB Models
class Transaction(Document):
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str

    class Settings:
        name = "trx_collection2"

# Request Models
class RequestNewTransaction(BaseModel):
    amount: int = Field(ge=1, description="Nominal transaksi minimal 1 Rupiah")
    method: Literal["cash", "gopay", "bca", "shopee", "mandiri"]
    desc: str
    trx_type: Literal["pembelian", "pemasukan"]
    trx_date: Optional[date] = None

class UpdateTransaction(BaseModel):
    amount: Optional[int] = Field(None, ge=1)
    method: Optional[Literal["cash", "gopay", "bca", "shopee", "mandiri"]] = None
    desc: Optional[str] = None
    trx_type: Optional[Literal["pembelian", "pemasukan"]] = None
    trx_date: Optional[date] = None

# Database Initialization
@app.on_event("startup")
async def init_db():
    # Ambil MONGO_URI dan MONGO_DB_NAME dari Env Var (Secret/ConfigMap OpenShift)
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb+srv://anggiangg30_db_user:AUwLOmQDXUdK0hI4@cluster0.0aoq9kb.mongodb.net/?appName=Cluster0"
    )
    db_name = os.getenv("MONGO_DB_NAME", "bootcamp")

    client = AsyncMongoClient(mongo_uri)
    
    # Akses database menggunakan DB_NAME
    database = client[db_name]

    await init_beanie(database=database, document_models=[Transaction])

# API Endpoints
@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    if request_body.trx_date:
        final_date = datetime.combine(request_body.trx_date, datetime.min.time())
    else:
        final_date = datetime.now()

    trx = Transaction(
        date=final_date, 
        amount=request_body.amount, 
        method=request_body.method, 
        desc=request_body.desc, 
        trx_type=request_body.trx_type
    )
    await trx.insert()

    # --- TRIGGER AUTOMATED PROFILING CHECK ---
    alert_info = None

    if request_body.trx_type == "pembelian":
        start_of_month = datetime(final_date.year, final_date.month, 1)
        if final_date.month == 12:
            end_of_month = datetime(final_date.year + 1, 1, 1)
        else:
            end_of_month = datetime(final_date.year, final_date.month + 1, 1)

        pipeline = [
            {
                "$match": {
                    "trx_type": "pembelian",
                    "date": {"$gte": start_of_month, "$lt": end_of_month}
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_pengeluaran": {"$sum": "$amount"}
                }
            }
        ]

        agg_result = await Transaction.aggregate(pipeline).to_list()
        total_bulan_ini = agg_result[0]["total_pengeluaran"] if agg_result else request_body.amount

        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(
                    f"{PROFILING_SERVICE_URL}/check-alert",
                    json={"total_pengeluaran_bulan_ini": float(total_bulan_ini)},
                    timeout=5.0
                )
                if res.status_code == 200:
                    alert_info = res.json()
        except Exception as e:
            print(f"[WARN] Profiling Service tidak terjangkau: {e}")

    return {
        "status": "success",
        "data": trx,
        "alert": alert_info
    }

@app.get("/transaction")
async def get_transaction(start_date: datetime, end_date: datetime):
    return await Transaction.find(
        Transaction.date >= start_date, Transaction.date <= end_date
    ).to_list()

@app.get("/transaction/summary")
async def summary_by_method(year: int, month: int):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    pipeline = [
        {"$match": {"date": {"$gte": start, "$lt": end}}},
        {"$group": {"_id": "$trx_type", "total_amount": {"$sum": "$amount"}}}
    ]

    summary_data = await Transaction.aggregate(pipeline).to_list()
    
    total_pemasukan = 0
    total_pengeluaran = 0
    
    for item in summary_data:
        if item["_id"] == "pemasukan":
            total_pemasukan = item["total_amount"]
        elif item["_id"] == "pembelian":
            total_pengeluaran = item["total_amount"]
            
    nett = total_pemasukan - total_pengeluaran
    rasio = 0.0
    
    if nett == 0:
        if total_pemasukan == 0 and total_pengeluaran == 0:
            status = "Belum Ada Transaksi"
            rasio = 0.0
        else:
            status = "Indikasi Big Spender"
            rasio = 1.0
    elif total_pemasukan > 0:
        rasio = total_pengeluaran / total_pemasukan
        if rasio < 1.0:
            status = "Big Saver Guy"
        else:
            status = "Reckless Spender"
    else:
        status = "Reckless Spender"
        rasio = None
        
    return {
        "periode": f"{year}-{month:02d}",
        "total_pemasukan": total_pemasukan,
        "total_pengeluaran": total_pengeluaran,
        "nett": nett,
        "rasio": rasio,
        "status": status
    }

@app.put("/transaction/{trx_id}")
async def update_transaction(trx_id: PydanticObjectId, req: UpdateTransaction):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    if req.amount is not None: trx.amount = req.amount
    if req.method is not None: trx.method = req.method
    if req.desc is not None: trx.desc = req.desc
    if req.trx_type is not None: trx.trx_type = req.trx_type
    if req.trx_date is not None: 
        trx.date = datetime.combine(req.trx_date, datetime.min.time())
        
    await trx.save()
    return trx

@app.delete("/transaction/{trx_id}")
async def delete_transaction(trx_id: PydanticObjectId):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    await trx.delete()
    return {"message": f"Transaction {trx_id} deleted successfully"}

@app.post("/transaction/import-excel")
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Format file harus Excel (.xlsx atau .xls)")

    try:
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        df = df.rename(columns={
            'datetime': 'date',
            'payment_method': 'method',
            'description': 'desc'
        })
        
        transactions_to_insert = []
        error_rows = []

        for index, row in df.iterrows():
            try:
                raw_amount = int(row['amount'])
                if raw_amount > 0:
                    tipe_trx = "pemasukan"
                elif raw_amount < 0:
                    tipe_trx = "pembelian"
                else:
                    raise ValueError("Amount tidak boleh 0")
                
                final_amount = abs(raw_amount)
                final_date = pd.to_datetime(row['date'])

                trx = Transaction(
                    date=final_date,
                    amount=final_amount,
                    method=str(row['method']).strip().lower(),
                    desc=str(row['desc']),
                    trx_type=tipe_trx
                )
                transactions_to_insert.append(trx)

            except Exception as e:
                error_rows.append(f"Baris {index + 2} gagal: {str(e)}")

        if transactions_to_insert:
            await Transaction.insert_many(transactions_to_insert)

        return {
            "message": f"Migrasi berhasil. {len(transactions_to_insert)} data tersimpan.",
            "failed_rows": len(error_rows),
            "errors": error_rows
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memproses file Excel: {str(e)}")