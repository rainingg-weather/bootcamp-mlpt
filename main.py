from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Literal, Optional
from beanie import Document, init_beanie, PydanticObjectId
from pymongo import AsyncMongoClient
import pandas as pd
import io
import os

app = FastAPI()

class Transaction(Document):
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str

    class Settings:
        name = "trx_collection"

# 1. Pydantic Model with strict validations
class RequestNewTransaction(BaseModel):
    # Minimum 1, discrete integer is handled by 'int' type
    amount: int = Field(ge=1, description="Nominal transaksi minimal 1 Rupiah")
    
    # Restrict to specific methods
    method: Literal["cash", "gopay", "bca", "shopee", "mandiri"]
    
    desc: str
    
    # Restrict to specific transaction types
    trx_type: Literal["pembelian", "pemasukan"]
    
    # Optional custom date using YYYY-MM-DD format
    trx_date: Optional[date] = None

class UpdateTransaction(BaseModel):
    amount: Optional[int] = Field(None, ge=1)
    method: Optional[Literal["cash", "gopay", "bca", "shopee", "mandiri"]] = None
    desc: Optional[str] = None
    trx_type: Optional[Literal["pembelian", "pemasukan"]] = None
    trx_date: Optional[date] = None

@app.on_event("startup")
async def init_db():
    # Ambil URI dari environment variable, gunakan default nilai jika tidak diset
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb+srv://anggiangg30_db_user:AUwLOmQDXUdK0hI4@cluster0.0aoq9kb.mongodb.net/?appName=Cluster0"
    )
    client = AsyncMongoClient(mongo_uri)
    await init_beanie(database=client.bootcamp, document_models=[Transaction])

@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    # Determine the date: use user input if provided, otherwise default to current time
    if request_body.trx_date:
        # Convert date to datetime to match the DB schema
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
    return trx

@app.get("/transaction")
async def get_transaction(start_date: datetime, end_date: datetime):
    return await Transaction.find(
        Transaction.date >= start_date, Transaction.date <= end_date
    ).to_list()

@app.get("/transaction/summary")
async def summary_by_method(year: int, month: int):
    # Menentukan rentang waktu bulan yang diminta
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    # Pipeline agregasi MongoDB (hanya berdasarkan nominal amount)
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lt": end}}},
        {"$group": {"_id": "$trx_type", "total_amount": {"$sum": "$amount"}}}
    ]

    summary_data = await Transaction.aggregate(pipeline).to_list()
    
    # Inisialisasi variabel
    total_pemasukan = 0
    total_pengeluaran = 0
    
    # Mapping data agregasi
    for item in summary_data:
        if item["_id"] == "pemasukan":
            total_pemasukan = item["total_amount"]
        elif item["_id"] == "pembelian":
            total_pengeluaran = item["total_amount"]
            
    # Menghitung nett
    nett = total_pemasukan - total_pengeluaran
    
    # Inisialisasi rasio default
    rasio = 0.0
    
    # KATEGORISASI BERDASARKAN RASIO
    # Jika nett = 0 (Pemasukan sama dengan pengeluaran)
    if nett == 0:
        if total_pemasukan == 0 and total_pengeluaran == 0:
             status = "Belum Ada Transaksi"
             rasio = 0.0
        else:
             status = "Indikasi Big Spender"
             rasio = 1.0 # Karena pengeluaran == pemasukan
             
    # Jika ada pemasukan, kita hitung rasionya (Pengeluaran dibagi Pemasukan)
    elif total_pemasukan > 0:
        rasio = total_pengeluaran / total_pemasukan
        if rasio < 1.0:
            status = "Big Saver Guy"
        else: # rasio > 1.0
            status = "Reckless Spender"
            
    # Jika tidak ada pemasukan sama sekali tapi ada pengeluaran
    else:
        status = "Reckless Spender"
        rasio = None # Set None (null di JSON) untuk menghindari ZeroDivisionError
        
    return {
        "periode": f"{year}-{month:02d}",
        "total_pemasukan": total_pemasukan,
        "total_pengeluaran": total_pengeluaran,
        "nett": nett,
        "rasio": rasio, # Tambahkan rasio ke output response
        "status": status
    }
# 2. UPDATE API to handle input mistakes
@app.put("/transaction/{trx_id}")
async def update_transaction(trx_id: PydanticObjectId, req: UpdateTransaction):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    # Update fields only if they are provided in the request
    if req.amount is not None: trx.amount = req.amount
    if req.method is not None: trx.method = req.method
    if req.desc is not None: trx.desc = req.desc
    if req.trx_type is not None: trx.trx_type = req.trx_type
    if req.trx_date is not None: 
        trx.date = datetime.combine(req.trx_date, datetime.min.time())
        
    await trx.save()
    return trx

# 3. DELETE API to remove errant transactions
@app.delete("/transaction/{trx_id}")
async def delete_transaction(trx_id: PydanticObjectId):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaction not found")
        
    await trx.delete()
    return {"message": f"Transaction {trx_id} deleted successfully"}

@app.post("/transaction/import-excel")
async def import_excel(file: UploadFile = File(...)):
    # Validasi ekstensi file
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Format file harus Excel (.xlsx atau .xls)")

    try:
        # Membaca file Excel dari upload user
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # Penyesuaian nama kolom bawaan Excel Ferdi ke schema Database
        df = df.rename(columns={
            'datetime': 'date',
            'payment_method': 'method',
            'description': 'desc'
        })
        
        transactions_to_insert = []
        error_rows = []

        for index, row in df.iterrows():
            try:
                # 1. UBAH FORMAT AMOUNT:
                # Pisahkan nilai minus/plus menjadi tipe transaksi, lalu jadikan amount absolut (positif)
                raw_amount = int(row['amount'])
                if raw_amount > 0:
                    tipe_trx = "pemasukan"
                elif raw_amount < 0:
                    tipe_trx = "pembelian"
                else:
                    raise ValueError("Amount tidak boleh 0")
                
                final_amount = abs(raw_amount)

                # 2. UBAH FORMAT TANGGAL:
                # Konversi string/format excel bawaan menjadi format datetime Python yang valid untuk MongoDB
                final_date = pd.to_datetime(row['date'])

                # Insert data dengan nilai yang sudah diformat, sisanya langsung dimasukkan
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

        # Simpan semua data yang berhasil diproses ke database
        if transactions_to_insert:
            await Transaction.insert_many(transactions_to_insert)

        return {
            "message": f"Migrasi berhasil. {len(transactions_to_insert)} data tersimpan.",
            "failed_rows": len(error_rows),
            "errors": error_rows
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memproses file Excel: {str(e)}")