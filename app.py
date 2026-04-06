from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import uuid
import requests as http_requests
from config import *
from urllib.parse import urlencode
import os

app = Flask(__name__)
app.secret_key = SECRET_KEY

REDIRECT_URI = "https://bloodbank-project-b3zs.onrender.com/login/callback"

# ================= DB =================
def get_db():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE
    )

# ================= BLOOD MATCH =================
def is_compatible(donor, recipient):
    rules = {
        "O-": ["O-","O+","A-","A+","B-","B+","AB-","AB+"],
        "O+": ["O+","A+","B+","AB+"],
        "A-": ["A-","A+","AB-","AB+"],
        "A+": ["A+","AB+"],
        "B-": ["B-","B+","AB-","AB+"],
        "B+": ["B+","AB+"],
        "AB-": ["AB-","AB+"],
        "AB+": ["AB+"]
    }
    return recipient in rules.get(donor, [])

# ================= HOME =================
@app.route('/')
def home():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM donors WHERE is_available=1")
    donors = cursor.fetchall()

    return render_template("index.html",
        donors=donors,
        user=session.get('user_name'),
        user_pic=session.get('user_pic')
    )

# ================= LOGIN =================
@app.route('/login')
def login():
    if session.get('logged_in'):
        return redirect('/')

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile"
    }

    google_login_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    return render_template("login.html", google_login_url=google_login_url)

# ================= CALLBACK =================
@app.route('/login/callback')
def callback():
    code = request.args.get('code')

    token = http_requests.post("https://oauth2.googleapis.com/token", data={
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code'
    }).json()

    user = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={'Authorization': 'Bearer ' + token['access_token']}
    ).json()

    session['user_name'] = user.get('name')
    session['user_pic'] = user.get('picture')
    session['logged_in'] = True

    if user.get('email') == "msci.2323@unigoa.ac.in":
        session['is_admin'] = True

    return redirect('/')

# ================= LOGOUT =================
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ================= DONOR =================
@app.route('/donor')
def donor():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM donors")
    donors = cursor.fetchall()

    return render_template("donor.html", donors=donors)

@app.route('/donor/register', methods=['POST'])
def register():
    db = get_db()
    cursor = db.cursor()

    donor_id = 'D' + str(uuid.uuid4())[:6].upper()

    cursor.execute("""
        INSERT INTO donors
        (donor_id, full_name, email, blood_type, phone, city, is_available)
        VALUES (%s,%s,%s,%s,%s,%s,1)
    """, (
        donor_id,
        request.form['full_name'],
        request.form['email'],
        request.form['blood_type'],
        request.form['phone'],
        request.form['city']
    ))

    db.commit()
    return redirect('/donor')

# ================= HOSPITAL =================
@app.route('/hospital', methods=['GET','POST'])
def hospital():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    matched = []

    if request.method == 'POST':
        blood = request.form['blood_type']

        cursor.execute("SELECT * FROM donors WHERE is_available=1")
        donors = cursor.fetchall()

        matched = [d for d in donors if is_compatible(d['blood_type'], blood)]

    cursor.execute("SELECT * FROM hospitals")
    hospitals = cursor.fetchall()

    return render_template("hospital.html",
        hospitals=hospitals,
        matched_donors=matched)

# ================= ADMIN =================
@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        return "Access Denied"
    return render_template("admin.html")

if __name__ == '__main__':
    app.run(debug=True)
