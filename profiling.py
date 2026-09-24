from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from pymongo import AsyncMongoClient
from datetime import datetime
import pandas as pd
import os

app = FastAPI(title="Financial Profiling Service")

# Konfigurasi MongoDB (Sama dengan main.py)
MONGO_URI = os.getenv(
    "MONGO_URI", 
    "mongodb+srv://anggiangg30_db_user:AUwLOmQDXUdK0hI4@cluster0.0aoq9kb.mongodb.net/?appName=Cluster0"
)

class AlertCheckRequest(BaseModel):
    total_pengeluaran_bulan_ini: float

async def calculate_historical_limit() -> tuple[Dict[str, float], Dict[str, Optional[float]], float]:
    """Membaca data transaksi historis dari MongoDB dan menghitung limit N-1."""
    client = AsyncMongoClient(MONGO_URI)
    db = client.bootcamp
    collection = db.trx_collection2

    # Ambil semua transaksi jenis pengeluaran/pembelian
    cursor = collection.find({"trx_type": "pembelian"})
    transactions = await cursor.to_list(length=10000)

    if not transactions:
        return {}, {}, 0.0

    # Ubah data MongoDB ke Pandas DataFrame
    df = pd.DataFrame(transactions)
    df['clean_date'] = pd.to_datetime(df['date'])
    df['clean_amount'] = df['amount'].abs()
    df['bulan_tahun'] = df['clean_date'].dt.strftime('%Y-%m')

    # Agregasi total pengeluaran per bulan
    grouped = df.groupby('bulan_tahun')['clean_amount'].sum().sort_index()
    monthly_expenses = grouped.to_dict()

    # Hitung Rerata Historis (Formula N-1)
    months = list(monthly_expenses.keys())
    historical_limits = {}
    accumulated_sum = 0.0

    for idx, month in enumerate(months):
        if idx == 0:
            historical_limits[month] = None # Bulan pertama belum ada acuan
        else:
            historical_limits[month] = round(accumulated_sum / idx, 2)
        accumulated_sum += monthly_expenses[month]

    # Batas pengeluaran bulan berjalan
    total_months = len(months)
    current_limit = round(accumulated_sum / total_months, 2) if total_months > 0 else 0.0

    return monthly_expenses, historical_limits, current_limit

@app.get("/summary")
async def get_profiling_summary():
    """Endpoint melihat ringkasan profiling berdasarkan data dari MongoDB Transaction Service."""
    monthly_expenses, historical_limits, current_limit = await calculate_historical_limit()

    summary = []
    for month, expense in monthly_expenses.items():
        limit = historical_limits.get(month)
        status = "DATA AWAL ACUAN"
        diff = 0.0

        if limit is not None:
            if expense > limit:
                status = "OVERBUDGET ⚠️"
                diff = expense - limit
            else:
                status = "SAFE ✅"
                diff = limit - expense

        summary.append({
            "bulan": month,
            "total_pengeluaran": expense,
            "batas_pengeluaran": limit,
            "status": status,
            "selisih": round(diff, 2)
        })

    return {
        "historis_bulan_lalu": summary,
        "batas_pengeluaran_bulan_ini": current_limit,
        "metode_kalkulasi": "Rerata aritmatika pengeluaran bulan-bulan sebelumnya dari MongoDB (N-1)"
    }

@app.post("/check-alert")
async def check_alert(req: AlertCheckRequest):
    """Endpoint trigger check alert yang dipanggil oleh main.py saat ada POST /transaction/add."""
    _, _, limit = await calculate_historical_limit()
    total_pengeluaran_bulan_ini = req.total_pengeluaran_bulan_ini

    if limit > 0 and total_pengeluaran_bulan_ini > limit:
        kelebihan = total_pengeluaran_bulan_ini - limit
        return {
            "is_alert": True,
            "warning_level": "WARNING",
            "message": (
                f"⚠️ Ups, pengeluaran kamu bulan ini sudah menyentuh Rp {total_pengeluaran_bulan_ini:,.0f}! "
                f"Ini melampaui target bulanan (Rp {limit:,.0f}) sebesar Rp {kelebihan:,.0f}. "
                f"Jangan berkecil hati, yuk lebih bijak lagi untuk sisa harinya! Tetap semangat! 💪🔥"
            ),
            "batas_pengeluaran": limit,
            "total_saat_ini": total_pengeluaran_bulan_ini,
            "kelebihan_budget": round(kelebihan, 2)
        }

    sisa = limit - total_pengeluaran_bulan_ini if limit > 0 else 0.0
    return {
        "is_alert": False,
        "warning_level": "NORMAL",
        "message": f"✅ Pengeluaran kamu masih aman di angka Rp {total_pengeluaran_bulan_ini:,.0f}. Pertahankan! 👍",
        "batas_pengeluaran": limit,
        "total_saat_ini": total_pengeluaran_bulan_ini,
        "sisa_budget": round(sisa, 2)
    }