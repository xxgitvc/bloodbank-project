from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import uuid
import os
import requests as http_requests
from config import *

app = Flask(__name__)
app.secret_key = SECRET_KEY

REDIRECT_URI = os.environ.get("BASE_URL") + "/login/callback"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

def get_db():
    return mysql.connector.connect(
        host=MYSQL_HOST, user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE)

@app.route('/')
def home():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as total FROM donors")
    donor_count = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM hospitals")
    hospital_count = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) as total FROM blood_requests")
    request_count = cursor.fetchone()['total']
    cursor.close()
    db.close()
    return render_template('index.html',
        user=session.get('user_name'),
        user_pic=session.get('user_pic'),
        donor_count=donor_count,
        hospital_count=hospital_count,
        request_count=request_count,
        maps_key=MAPS_API_KEY)

@app.route('/login')
def login():
    if session.get('logged_in'):
        return redirect(url_for('home'))
    google_login_url = (
        GOOGLE_AUTH_URL +
        "?client_id=" + GOOGLE_CLIENT_ID +
        "&redirect_uri=" + REDIRECT_URI +
        "&response_type=code" +
        "&scope=openid email profile" +
        "&access_type=offline")
    return render_template('login.html',
        google_login_url=google_login_url,
        error=None)

@app.route('/login/callback')
def login_callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('login'))
    token_resp = http_requests.post(
        GOOGLE_TOKEN_URL, data={
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'})
    token_data = token_resp.json()
    access_token = token_data.get('access_token')
    if not access_token:
        return redirect(url_for('login'))
    user_resp = http_requests.get(
        GOOGLE_USER_URL,
        headers={'Authorization': 'Bearer ' + access_token})
    user_info = user_resp.json()
    session['user_name'] = user_info.get('name')
    session['user_email'] = user_info.get('email')
    session['user_pic'] = user_info.get('picture')
    session['logged_in'] = True
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/donor')
def donor():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM donors ORDER BY created_at DESC")
    donors = cursor.fetchall()
    donor_count = len(donors)
    available_count = sum(1 for d in donors if d['is_available'])
    cursor.close()
    db.close()
    return render_template('donor.html',
        donors=donors,
        donor_count=donor_count,
        available_count=available_count,
        user=session.get('user_name'),
        user_pic=session.get('user_pic'))

@app.route('/donor/register', methods=['POST'])
def donor_register():
    db = get_db()
    cursor = db.cursor()
    donor_id = 'D' + str(uuid.uuid4())[:6].upper()
    city = request.form['city']
    city_coords = {
        'Panaji': (15.4909, 73.8278),
        'Margao': (15.2832, 73.9862),
        'Vasco': (15.3982, 73.8111),
        'Mapusa': (15.5957, 73.8145),
        'Ponda': (15.4037, 74.0093),
        'Calangute': (15.5440, 73.7528),
        'Pernem': (15.7197, 73.7982),
        'Canacona': (15.0142, 74.0308),
        'Bicholim': (15.5957, 73.9503),
        'Quepem': (15.2122, 74.0772)}
    lat, lng = city_coords.get(city, (15.2993, 74.1240))
    cursor.execute("""
        INSERT INTO donors
        (donor_id, full_name, email,
        blood_type, phone, city,
        latitude, longitude,
        is_available, total_donations)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,0)""",
        (donor_id,
        request.form['full_name'],
        request.form['email'],
        request.form['blood_type'],
        request.form['phone'],
        city, lat, lng))
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for('donor'))

@app.route('/hospital')
def hospital():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM hospitals ORDER BY city")
    hospitals = cursor.fetchall()
    cursor.execute("SELECT * FROM blood_inventory WHERE status='AVAILABLE'")
    inventory = cursor.fetchall()
    cursor.execute("""
        SELECT r.*, h.hospital_name
        FROM blood_requests r
        JOIN hospitals h
        ON r.hospital_id = h.hospital_id
        ORDER BY r.requested_at DESC""")
    requests_list = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('hospital.html',
        hospitals=hospitals,
        inventory=inventory,
        requests=requests_list,
        hospital_count=len(hospitals),
        inventory_count=len(inventory),
        request_count=len(requests_list),
        pending_count=sum(1 for r in requests_list if r['status']=='PENDING'),
        user=session.get('user_name'),
        user_pic=session.get('user_pic'))

@app.route('/hospital/request', methods=['POST'])
def hospital_request():
    db = get_db()
    cursor = db.cursor()
    request_id = 'R' + str(uuid.uuid4())[:6].upper()
    cursor.execute("""
        INSERT INTO blood_requests
        (request_id, hospital_id,
        blood_type, units_needed,
        urgency, status)
        VALUES (%s,%s,%s,%s,%s,'PENDING')""",
        (request_id,
        request.form['hospital_id'],
        request.form['blood_type'],
        request.form['units_needed'],
        request.form['urgency']))
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for('hospital'))

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM donors ORDER BY created_at DESC")
    donors = cursor.fetchall()
    cursor.execute("SELECT * FROM hospitals ORDER BY city")
    hospitals = cursor.fetchall()
    cursor.execute("""
        SELECT r.*, h.hospital_name
        FROM blood_requests r
        JOIN hospitals h
        ON r.hospital_id = h.hospital_id
        ORDER BY r.requested_at DESC""")
    requests_list = cursor.fetchall()
    cursor.execute("SELECT * FROM blood_inventory")
    inventory = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('admin.html',
        donors=donors,
        hospitals=hospitals,
        requests=requests_list,
        inventory=inventory,
        donor_count=len(donors),
        available_count=sum(1 for d in donors if d['is_available']),
        hospital_count=len(hospitals),
        inventory_count=len(inventory),
        request_count=len(requests_list),
        pending_count=sum(1 for r in requests_list if r['status']=='PENDING'),
        user=session.get('user_name'),
        user_pic=session.get('user_pic'))

@app.route('/admin/fulfill/<request_id>', methods=['POST'])
def fulfill_request(request_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE blood_requests SET status='FULFILLED' WHERE request_id=%s", (request_id,))
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for('admin'))

@app.route('/admin/cancel/<request_id>', methods=['POST'])
def cancel_request(request_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE blood_requests SET status='CANCELLED' WHERE request_id=%s", (request_id,))
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
