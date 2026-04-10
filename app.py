from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector.pooling
import uuid
import os
import math
import json
import requests as http_requests
from config import *

# GCP Pub/Sub
from google.cloud import pubsub_v1

app = Flask(__name__)
app.secret_key = SECRET_KEY

BASE_URL     = os.environ.get("BASE_URL", "http://localhost:8080")
REDIRECT_URI = BASE_URL + "/login/callback"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"

# ── Pub/Sub config ───────────────────────────────────────────────────────────
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "your-gcp-project-id")
PUBSUB_TOPIC   = "blood-request-alerts"

# Initialise ONCE at startup — not per request
publisher  = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(GCP_PROJECT_ID, PUBSUB_TOPIC)


def publish_alert(payload: dict):
    """Publish a blood-request alert to GCP Pub/Sub."""
    try:
        data   = json.dumps(payload).encode("utf-8")
        future = publisher.publish(topic_path, data)
        print(f"✅ Pub/Sub message published: {future.result()}")
    except Exception as e:
        print(f"❌ Pub/Sub publish failed: {e}")


# ── DATABASE connection pool ─────────────────────────────────────────────────
# Pool is created ONCE at startup; get_db() checks out a connection from it.
db_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="bloodbank_pool",
    pool_size=5,
    host=MYSQL_HOST,
    user=MYSQL_USER,
    password=MYSQL_PASSWORD,
    database=MYSQL_DATABASE,
)


def get_db():
    """Return a pooled connection. Usage is identical to before."""
    return db_pool.get_connection()


# ── MATCHING LOGIC ───────────────────────────────────────────────────────────
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
    """
    Find the nearest available donor compatible with blood_type,
    using the requesting hospital's actual coordinates.
    """
    compatible   = COMPATIBLE_DONORS.get(blood_type, [blood_type])
    placeholders = ", ".join(["%s"] * len(compatible))

    db     = get_db()
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

    return nearest, round(min_dist * 111, 2)   # degrees → km


# ── MAP SUPPORT ──────────────────────────────────────────────────────────────
def get_all_donors():
    db     = get_db()
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
    db     = get_db()
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
    db     = get_db()
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
    db     = get_db()
    cursor = db.cursor()

    donor_id = "D" + str(uuid.uuid4())[:6].upper()
    city     = request.form["city"]

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
    db     = get_db()
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
    db     = get_db()
    cursor = db.cursor(dictionary=True)

    request_id  = "R" + str(uuid.uuid4())[:6].upper()
    hospital_id = request.form["hospital_id"]
    blood_type  = request.form["blood_type"]
    units       = request.form["units_needed"]
    urgency     = request.form["urgency"]

    # ── 1. Fetch requesting hospital's real coordinates ───────────────────────
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
        # Fallback to Goa centroid if coords missing
        h_lat, h_lon = 15.2993, 74.1240
        h_name = hospital_id

    # ── 2. Insert request into DB ─────────────────────────────────────────────
    cursor.execute("""
        INSERT INTO blood_requests
        (request_id, hospital_id, blood_type, units_needed, urgency, status)
        VALUES (%s,%s,%s,%s,%s,'PENDING')
    """, (request_id, hospital_id, blood_type, units, urgency))
    db.commit()

    cursor.close()
    db.close()

    # ── 3. Run matching engine ────────────────────────────────────────────────
    nearest, dist_km = find_nearest_donor(blood_type, h_lat, h_lon)

    if nearest:
        print(f"✅ MATCH: {nearest['full_name']} ({nearest['blood_type']}) — {dist_km} km away")

        # ── 4. Publish alert to Pub/Sub ───────────────────────────────────────
        publish_alert({
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
        })

        # ── 5. Optionally mark donor as temporarily unavailable ───────────────
        # Uncomment to prevent double-matching on the same donor:
        # db2     = get_db()
        # cursor2 = db2.cursor()
        # cursor2.execute(
        #     "UPDATE donors SET is_available = 0 WHERE donor_id = %s",
        #     (nearest["donor_id"],)
        # )
        # db2.commit()
        # cursor2.close()
        # db2.close()

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

    return redirect(url_for("hospital", success=1))


# ── Pub/Sub PUSH ENDPOINT (subscriber webhook) ────────────────────────────────
@app.route("/pubsub/push", methods=["POST"])
def pubsub_push():
    """
    GCP Pub/Sub push subscription calls this endpoint whenever a message
    is published to the blood-request-alerts topic.

    Configure in GCP Console:
      Pub/Sub → Subscriptions → Create subscription
        Delivery type : Push
        Endpoint URL  : https://<your-cloud-run-url>/pubsub/push
    """
    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        return "Bad Request", 400

    import base64
    raw   = envelope["message"].get("data", "")
    data  = json.loads(base64.b64decode(raw).decode("utf-8"))
    event = data.get("event")

    print(f"📨 Pub/Sub push received: {event}")

    if event == "BLOOD_REQUEST_MATCHED":
        # ── Example: send SMS via Twilio / email via SendGrid ─────────────────
        # send_sms(data["donor_phone"], build_donor_sms(data))
        # send_email(data["donor_email"], build_donor_email(data))
        print(
            f"  → Donor {data['donor_name']} ({data['donor_phone']}) "
            f"needed at {data['hospital_name']} for {data['blood_type']}"
        )

    elif event == "BLOOD_REQUEST_NO_MATCH":
        # ── Example: alert admin via email ────────────────────────────────────
        # send_admin_alert(data)
        print(f"  → No match for {data['blood_type']} at {data['hospital_name']}")

    # Must return 2xx so Pub/Sub doesn't retry
    return "OK", 200


# ── ADMIN ──────────────────────────────────────────────────────────────────────
@app.route("/admin")
def admin():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    db     = get_db()
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
    db     = get_db()
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
    db     = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE blood_requests SET status='CANCELLED' WHERE request_id=%s",
        (request_id,)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(url_for("admin"))


# ── RUN ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
