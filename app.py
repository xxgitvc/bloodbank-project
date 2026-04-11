from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
import uuid
import os
import math
import json
import base64
import smtplib
import requests as http_requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import *

# GCP Pub/Sub
from google.cloud import pubsub_v1

app = Flask(__name__)
app.secret_key = SECRET_KEY

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
REDIRECT_URI = BASE_URL + "/login/callback"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"

# ── Pub/Sub config ────────────────────────────────────────────────────────────
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "your-gcp-project-id")
PUBSUB_TOPIC   = "blood-request-alerts"

# ── Email config ──────────────────────────────────────────────────────────────
GMAIL_SENDER       = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_EMAIL        = os.environ.get("ALERT_EMAIL", "msci.2323@unigoa.ac.in")

# ── Debug: print env vars on startup ─────────────────────────────────────────
print(f"🔧 GCP_PROJECT_ID     : {os.environ.get('GCP_PROJECT_ID', 'NOT SET')}")
print(f"🔧 GMAIL_SENDER       : {os.environ.get('GMAIL_SENDER', 'NOT SET')}")
print(f"🔧 GMAIL_APP_PASSWORD : {'SET ✅' if os.environ.get('GMAIL_APP_PASSWORD') else 'NOT SET ❌'}")
print(f"🔧 ALERT_EMAIL        : {os.environ.get('ALERT_EMAIL', 'NOT SET')}")


# ── GMAIL SMTP ────────────────────────────────────────────────────────────────
def send_gmail(subject, body, to_email):
    """Send email via Gmail SMTP using App Password."""
    try:
        print(f"📧 Attempting to send email...")
        print(f"📧 From    : {GMAIL_SENDER}")
        print(f"📧 To      : {to_email}")
        print(f"📧 Subject : {subject}")

        msg = MIMEMultipart()
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())

        print(f"✅ Email sent successfully to {to_email}")

    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Email auth failed (check app password): {e}")
    except smtplib.SMTPException as e:
        print(f"❌ SMTP error: {e}")
    except Exception as e:
        print(f"❌ Email failed (unexpected error): {e}")


def send_matched_email(data):
    subject = (
        f"🚨 Blood Request [{data['urgency']}] - "
        f"{data['blood_type']} needed at {data['hospital_name']}"
    )
    body = f"""
🩸 BLOOD REQUEST MATCHED — BloodBank Goa
==========================================

Request ID   : {data['request_id']}
Hospital     : {data['hospital_name']}
Blood Type   : {data['blood_type']}
Units Needed : {data['units_needed']}
Urgency      : {data['urgency']}

✅ MATCHED DONOR DETAILS:
--------------------------
Name         : {data['donor_name']}
Phone        : {data['donor_phone']}
Email        : {data['donor_email']}
City         : {data['donor_city']}
Distance     : {data['distance_km']} km from hospital

⚠️  Please contact the donor immediately!

--
BloodBank Goa — Powered by Google Cloud Platform
    """
    send_gmail(subject, body, ALERT_EMAIL)


def send_no_match_email(data):
    subject = (
        f"⚠️ No Donor Found - "
        f"{data['blood_type']} at {data['hospital_name']}"
    )
    body = f"""
❌ NO DONOR MATCH FOUND — BloodBank Goa
==========================================

Request ID   : {data['request_id']}
Hospital     : {data['hospital_name']}
Blood Type   : {data['blood_type']}
Units Needed : {data['units_needed']}
Urgency      : {data['urgency']}

No compatible donor is currently available.
Please check the admin panel immediately and
consider reaching out to nearby blood banks.

--
BloodBank Goa — Powered by Google Cloud Platform
    """
    send_gmail(subject, body, ALERT_EMAIL)


# ── Pub/Sub PUBLISH ───────────────────────────────────────────────────────────
def publish_alert(payload: dict):
    """Publish a blood-request alert to GCP Pub/Sub."""
    try:
        publisher  = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC)
        data       = json.dumps(payload).encode("utf-8")
        future     = publisher.publish(topic_path, data)
        print(f"✅ Pub/Sub message published: {future.result()}")
    except Exception as e:
        print(f"❌ Pub/Sub publish failed: {e}")


# ── DATABASE ──────────────────────────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE
    )


# ── MATCHING LOGIC ────────────────────────────────────────────────────────────
COMPATIBLE_DONORS = {
    "A+":  ["A+", "A-", "O+", "O-"],
    "A-":  ["A-", "O-"],
    "B+":  ["B+", "B-", "O+", "O-"],
    "B-":  ["B-", "O-"],
    "AB+": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    "AB-": ["A-", "B-", "AB-", "O-"],
    "O+":  ["O+", "O-"],
    "O-":  ["O-"],
}

def calculate_distance(lat1, lon1, lat2, lon2):
    """Euclidean distance in degrees (fine for Goa's scale)."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)


def find_nearest_donor(blood_type, hospital_lat, hospital_lon):
    compatible   = COMPATIBLE_DONORS.get(blood_type, [blood_type])
    placeholders = ", ".join(["%s"] * len(compatible))

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(f"""
        SELECT * FROM donors
        WHERE blood_type IN ({placeholders})
          AND is_available = 1
    """, compatible)
    donors = cursor.fetchall()
    cursor.close()
    db.close()

    nearest  = None
    min_dist = float("inf")

    for d in donors:
        dist = calculate_distance(
            hospital_lat, hospital_lon,
            float(d["latitude"]), float(d["longitude"])
        )
        if dist < min_dist:
            min_dist = dist
            nearest  = d

    return nearest, round(min_dist * 111, 2)


# ── MAP SUPPORT ───────────────────────────────────────────────────────────────
def get_all_donors():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT full_name, blood_type, latitude, longitude
        FROM donors WHERE is_available = 1
    """)
    donors = cursor.fetchall()
    cursor.close()
    db.close()
    return donors


# ── HOME ──────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) as total FROM donors")
    donor_count = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as total FROM hospitals")
    hospital_count = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as total FROM blood_requests")
    request_count = cursor.fetchone()["total"]

    cursor.close()
    db.close()

    donors = get_all_donors()

    return render_template(
        "index.html",
        user=session.get("user_name"),
        user_pic=session.get("user_pic"),
        donor_count=donor_count,
        hospital_count=hospital_count,
        request_count=request_count,
        maps_key=MAPS_API_KEY,
        donors=donors,
    )


# ── LOGIN ─────────────────────────────────────────────────────────────────────
@app.route("/login")
def login():
    if session.get("logged_in"):
        return redirect(url_for("home"))

    google_login_url = (
        GOOGLE_AUTH_URL
        + "?client_id=" + GOOGLE_CLIENT_ID
        + "&redirect_uri=" + REDIRECT_URI
        + "&response_type=code"
        + "&scope=openid email profile"
        + "&access_type=offline"
    )
    return render_template("login.html", google_login_url=google_login_url, error=None)


@app.route("/login/callback")
def login_callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login"))

    token_resp = http_requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
    )
    token_data   = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return redirect(url_for("login"))

    user_resp = http_requests.get(
        GOOGLE_USER_URL,
        headers={"Authorization": "Bearer " + access_token},
    )
    user_info = user_resp.json()

    session["user_name"]  = user_info.get("name")
    session["user_email"] = user_info.get("email")
    session["user_pic"]   = user_info.get("picture")
    session["logged_in"]  = True

    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ── DONOR ─────────────────────────────────────────────────────────────────────
ADMIN_EMAILS = [
    "msci.2323@unigoa.ac.in",
    "msci.2312@unigoa.ac.in",
]

@app.route("/donor")
def donor():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    user_email = session.get("user_email")
    is_admin   = user_email in ADMIN_EMAILS

    donors = []
    if is_admin:
        cursor.execute("SELECT * FROM donors ORDER BY created_at DESC")
        donors = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) as total FROM donors")
    donor_count = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as total FROM donors WHERE is_available = 1")
    available_count = cursor.fetchone()["total"]

    cursor.close()
    db.close()

    return render_template(
        "donor.html",
        donors=donors,
        donor_count=donor_count,
        available_count=available_count,
        is_admin=is_admin,
        user=session.get("user_name"),
        user_pic=session.get("user_pic"),
    )


@app.route("/donor/register", methods=["POST"])
def donor_register():
    db = get_db()
    cursor = db.cursor()

    donor_id = "D" + str(uuid.uuid4())[:6].upper()
    city = request.form["city"]

    city_coords = {
        "Panaji": (15.4909, 73.8278),
        "Margao": (15.2832, 73.9862),
        "Vasco":  (15.3982, 73.8111),
        "Mapusa": (15.5957, 73.8145),
        "Ponda":  (15.4037, 74.0093),
    }
    lat, lng = city_coords.get(city, (15.2993, 74.1240))

    cursor.execute("""
        INSERT INTO donors
        (donor_id, full_name, email, blood_type, phone, city,
         latitude, longitude, is_available, total_donations)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,0)
    """, (
        donor_id,
        request.form["full_name"],
        request.form["email"],
        request.form["blood_type"],
        request.form["phone"],
        city, lat, lng,
    ))

    db.commit()
    cursor.close()
    db.close()

    return redirect(url_for("donor", success=1))


# ── HOSPITAL ──────────────────────────────────────────────────────────────────
@app.route("/hospital")
@app.route("/hospital/")
def hospital():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    user_email = session.get("user_email")
    is_admin   = user_email in ADMIN_EMAILS

    cursor.execute("SELECT * FROM hospitals ORDER BY city")
    hospitals = cursor.fetchall()

    cursor.execute("SELECT * FROM blood_inventory WHERE status='AVAILABLE'")
    inventory = cursor.fetchall()

    requests_list = []
    if is_admin:
        cursor.execute("""
            SELECT r.*, h.hospital_name
            FROM blood_requests r
            JOIN hospitals h ON r.hospital_id = h.hospital_id
            ORDER BY r.requested_at DESC
        """)
        requests_list = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "hospital.html",
        hospitals=hospitals,
        inventory=inventory,
        requests=requests_list,
        hospital_count=len(hospitals),
        inventory_count=len(inventory),
        request_count=len(requests_list),
        pending_count=sum(1 for r in requests_list if r["status"] == "PENDING"),
        is_admin=is_admin,
        user=session.get("user_name"),
        user_pic=session.get("user_pic"),
    )


# ── HOSPITAL REQUEST (with matching + Pub/Sub) ────────────────────────────────
@app.route("/hospital/request", methods=["POST"])
def hospital_request():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    request_id  = "R" + str(uuid.uuid4())[:6].upper()
    hospital_id = request.form["hospital_id"]
    blood_type  = request.form["blood_type"]
    units       = request.form["units_needed"]
    urgency     = request.form["urgency"]

    # 1. Fetch hospital coordinates
    cursor.execute(
        "SELECT hospital_name, latitude, longitude FROM hospitals WHERE hospital_id = %s",
        (hospital_id,)
    )
    hospital_row = cursor.fetchone()

    if hospital_row and hospital_row["latitude"] and hospital_row["longitude"]:
        h_lat  = float(hospital_row["latitude"])
        h_lon  = float(hospital_row["longitude"])
        h_name = hospital_row["hospital_name"]
    else:
        h_lat, h_lon = 15.2993, 74.1240
        h_name = hospital_id

    # 2. Insert request into DB
    cursor.execute("""
        INSERT INTO blood_requests
        (request_id, hospital_id, blood_type, units_needed, urgency, status)
        VALUES (%s,%s,%s,%s,%s,'PENDING')
    """, (request_id, hospital_id, blood_type, units, urgency))
    db.commit()

    # 3. Run matching engine
    nearest, dist_km = find_nearest_donor(blood_type, h_lat, h_lon)

    if nearest:
        print(f"✅ MATCH: {nearest['full_name']} ({nearest['blood_type']}) — {dist_km} km away")

        payload = {
            "event":         "BLOOD_REQUEST_MATCHED",
            "request_id":    request_id,
            "hospital_id":   hospital_id,
            "hospital_name": h_name,
            "blood_type":    blood_type,
            "units_needed":  units,
            "urgency":       urgency,
            "donor_id":      nearest["donor_id"],
            "donor_name":    nearest["full_name"],
            "donor_phone":   nearest["phone"],
            "donor_email":   nearest["email"],
            "donor_city":    nearest["city"],
            "distance_km":   dist_km,
        }
        publish_alert(payload)

    else:
        print(f"❌ NO MATCH found for blood type {blood_type} near {h_name}")

        publish_alert({
            "event":         "BLOOD_REQUEST_NO_MATCH",
            "request_id":    request_id,
            "hospital_id":   hospital_id,
            "hospital_name": h_name,
            "blood_type":    blood_type,
            "units_needed":  units,
            "urgency":       urgency,
        })

    cursor.close()
    db.close()

    return redirect(url_for("hospital", success=1))


# ── Pub/Sub PUSH ENDPOINT ─────────────────────────────────────────────────────
@app.route("/pubsub/push", methods=["POST"])
def pubsub_push():
    """
    GCP Pub/Sub push subscription calls this endpoint whenever a message
    is published to the blood-request-alerts topic.
    """
    print("📨 pubsub_push endpoint called")

    envelope = request.get_json(silent=True)
    print(f"📨 envelope received: {envelope}")

    if not envelope:
        print("❌ No envelope / empty body received")
        return "Bad Request", 400

    if "message" not in envelope:
        print(f"❌ No 'message' key. Keys found: {list(envelope.keys())}")
        return "Bad Request", 400

    raw = envelope["message"].get("data", "")
    print(f"📨 raw base64 data: {raw}")

    try:
        data  = json.loads(base64.b64decode(raw).decode("utf-8"))
        event = data.get("event")
        print(f"📨 Decoded event  : {event}")
        print(f"📨 Full payload   : {data}")
    except Exception as e:
        print(f"❌ Failed to decode message: {e}")
        return "Bad Request", 400

    if event == "BLOOD_REQUEST_MATCHED":
        print("📧 Sending MATCHED email alert...")
        send_matched_email(data)

    elif event == "BLOOD_REQUEST_NO_MATCH":
        print("📧 Sending NO MATCH email alert...")
        send_no_match_email(data)

    else:
        print(f"⚠️ Unknown event type: {event}")

    # Must return 2xx so Pub/Sub doesn't retry
    return "OK", 200


# ── ADMIN ─────────────────────────────────────────────────────────────────────
@app.route("/admin")
def admin():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM donors ORDER BY created_at DESC")
    donors = cursor.fetchall()

    cursor.execute("SELECT * FROM hospitals ORDER BY city")
    hospitals = cursor.fetchall()

    cursor.execute("""
        SELECT r.*, h.hospital_name
        FROM blood_requests r
        JOIN hospitals h ON r.hospital_id = h.hospital_id
        ORDER BY r.requested_at DESC
    """)
    requests_list = cursor.fetchall()

    cursor.execute("SELECT * FROM blood_inventory")
    inventory = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "admin.html",
        donors=donors,
        hospitals=hospitals,
        requests=requests_list,
        inventory=inventory,
        donor_count=len(donors),
        available_count=sum(1 for d in donors if d["is_available"]),
        hospital_count=len(hospitals),
        inventory_count=len(inventory),
        request_count=len(requests_list),
        pending_count=sum(1 for r in requests_list if r["status"] == "PENDING"),
        user=session.get("user_name"),
        user_pic=session.get("user_pic"),
    )


@app.route("/admin/fulfill/<request_id>", methods=["POST"])
def fulfill_request(request_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE blood_requests SET status='FULFILLED' WHERE request_id=%s",
        (request_id,)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for("admin"))


@app.route("/admin/cancel/<request_id>", methods=["POST"])
def cancel_request(request_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE blood_requests SET status='CANCELLED' WHERE request_id=%s",
        (request_id,)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for("admin"))


# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
