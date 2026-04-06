from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import uuid
import requests as http_requests
from config import *

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ================= GOOGLE CONFIG =================
REDIRECT_URI = "https://bloodbank-project-b3zs.onrender.com/login/callback"

# ================= DB =================
def get_db():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE
    )

# ================= HOME =================
@app.route('/')
def home():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT full_name, city, blood_type, latitude, longitude
        FROM donors
        WHERE is_available = 1
    """)
    donors = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "index.html",
        donors=donors,
        user=session.get('user_name'),
        user_pic=session.get('user_pic')
    )

# ================= LOGIN =================
@app.route('/login')
def login():
    if session.get('logged_in'):
        return redirect('/')

    google_login_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=" + GOOGLE_CLIENT_ID +
        "&redirect_uri=" + REDIRECT_URI +
        "&response_type=code" +
        "&scope=openid%20email%20profile" +
        "&access_type=offline" +
        "&prompt=consent"
    )

    return render_template("login.html", google_login_url=google_login_url)

# ================= CALLBACK =================
@app.route('/login/callback')
def login_callback():
    code = request.args.get('code')

    if not code:
        return "No code received"

    try:
        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                'code': code,
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'redirect_uri': REDIRECT_URI,
                'grant_type': 'authorization_code'
            }
        )

        token_data = token_resp.json()

        if 'access_token' not in token_data:
            return f"Token Error: {token_data}"

        access_token = token_data['access_token']

        user_resp = http_requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={'Authorization': 'Bearer ' + access_token}
        )

        user_info = user_resp.json()

        # 🔥 IMPORTANT FIX
        session.clear()

        session['user_name'] = user_info.get('name')
        session['user_email'] = user_info.get('email')
        session['user_pic'] = user_info.get('picture')
        session['logged_in'] = True

        # ADMIN CHECK
        email = user_info.get('email')
        session['is_admin'] = (
            email and email.strip().lower() == "msci.2323@unigoa.ac.in"
        )

        return redirect('/')

    except Exception as e:
        return f"Error: {str(e)}"

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

    cursor.execute("SELECT * FROM donors ORDER BY created_at DESC")
    donors = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template("donor.html", donors=donors)

@app.route('/donor/register', methods=['POST'])
def donor_register():
    db = get_db()
    cursor = db.cursor()

    donor_id = 'D' + str(uuid.uuid4())[:6].upper()

    city_coords = {
        'Panaji': (15.4909, 73.8278),
        'Margao': (15.2832, 73.9862),
        'Vasco': (15.3982, 73.8111),
        'Mapusa': (15.5957, 73.8145),
        'Ponda': (15.4037, 74.0093),
        'Calangute': (15.5440, 73.7528),
        'Canacona': (15.0142, 74.0308)
    }

    city = request.form['city']
    lat, lng = city_coords.get(city, (15.2993, 74.1240))

    cursor.execute("""
        INSERT INTO donors
        (donor_id, full_name, email, blood_type,
         phone, city, latitude, longitude, is_available)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)
    """, (
        donor_id,
        request.form['full_name'],
        request.form['email'],
        request.form['blood_type'],
        request.form['phone'],
        city, lat, lng
    ))

    db.commit()
    cursor.close()
    db.close()

    return redirect(url_for('donor'))

# ================= HOSPITAL =================
@app.route('/hospital')
def hospital():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM hospitals")
    hospitals = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template("hospital.html", hospitals=hospitals)

# ================= ADMIN =================
@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        return redirect('/login')

    if not session.get('is_admin'):
        return "Access Denied"

    return render_template("admin.html")

# ================= RUN =================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
