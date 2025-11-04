# main_final.py
import os
import requests
import stripe
import bcrypt
import jwt
import google.generativeai as genai
import json
import re 

from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request , Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
from dateutil import parser


# -------------------------------
# Load environment variables
# -------------------------------
load_dotenv()

# Secure configuration variables from environment

# Google App Script Web Hook fetched from .env
# If hardcoded, it can be commented here
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK")
# Hardcoded Google Sheet webhook (Umang's Apps Script URL)
# If Web Hook link fetched from .env, it can be commented here
# GOOGLE_SHEET_WEBHOOK = "https://script.google.com/macros/s/AKfycbyuq_51jNTV_t_sG0N-ozhpm_tCeVaOv1CDBYcdVQ4uO-c9WWTdRu1sqxW__QcCklh-ZQ/exec"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "fiftyshadesofgravysecret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Input validation
if not (GOOGLE_SHEET_WEBHOOK and STRIPE_SECRET_KEY and GEMINI_API_KEY):
    raise Exception("❌ Missing environment variables. Please check your .env file.")

# Initialize external services
stripe.api_key = STRIPE_SECRET_KEY
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

# -------------------------------
# FastAPI App Setup
# -------------------------------
app = FastAPI(title="Fifty Shades Of Gravy Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for simplicity (use specific domains in production)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Utility Functions (Authentication & Data)
# -------------------------------

def normalize_email(email: str) -> str:
    """Standardize email formatting."""
    return (email or "").strip().lower()

def get_sheet_data(sheet_name: str) -> List[Dict[str, str]]:
    """Fetch all rows from a specified Google Sheet via webhook."""
    try:
        url = f"{GOOGLE_SHEET_WEBHOOK}?sheet={sheet_name}"
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        return res.json().get("data", [])
    except Exception as e:
        print(f"Error fetching {sheet_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch {sheet_name} data.")

def append_to_sheet(sheet_name: str, data: Dict[str, Any]):
    """
    ✅ Append or update data to a specific Google Sheet via Google Apps Script Webhook.
    Automatically integrates with Table Booking Logic on the Apps Script side.
    """
    try:
        url = f"{GOOGLE_SHEET_WEBHOOK}?sheet={sheet_name}"
        headers = {"Content-Type": "application/json"}

        # Clean None values (Apps Script can't handle them)
        clean_data = {k: (v if v is not None else "") for k, v in data.items()}

        print("➡️ Sending data to Google Sheet...")
        print(f"📄 Sheet Name: {sheet_name}")
        print(f"🌐 URL: {url}")
        print(f"📦 Data: {clean_data}")

        res = requests.post(url, json=clean_data, headers=headers, timeout=10)
        print(f"📨 Raw Response: {res.text}")

        res.raise_for_status()

        # Parse response safely
        try:
            result = res.json()
        except Exception as parse_err:
            print("⚠️ Could not parse JSON:", parse_err)
            return {"status": "error", "message": res.text}

        if result.get("status") == "success":
            print(f"✅ Successfully appended data to '{sheet_name}'.")
        else:
            print(f"⚠️ Google Sheet responded with: {result}")

        return result

    except Exception as e:
        print(f"❌ Error appending to {sheet_name}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to append data to '{sheet_name}' sheet. {e}"
        )



def hash_password(password: str) -> str:
    """Hashes a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    """Verifies a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_jwt(email: str) -> str:
    """Generates a JWT token for the user."""
    payload = {"email": email, "exp": datetime.utcnow() + timedelta(days=1)}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # Ensure the result is a string for return
    return token.decode("utf-8") if isinstance(token, bytes) else token

def find_user_by_email(email: str) -> Optional[Dict[str, str]]:
    """Finds a user in the 'users' sheet by their email."""
    email = normalize_email(email)
    users = get_sheet_data("users")
    for u in users:
        if normalize_email(u.get("Email") or "") == email:
            return u
    return None
def get_available_table() -> Optional[str]:
    """Returns the first available table ID (e.g., T6) and marks it as booked."""
    tables = get_sheet_data("table")
    for t in tables:
        table_no = t.get("Table")
        available = t.get("Availability", "").strip().lower()
        if available == "yes":
            # Mark this table as booked
            update_data = {"Table": table_no, "Availability": "No"}
            append_to_sheet("table", update_data)  # or send a webhook update if you support PUT
            return table_no
    return None


def get_active_booking(email: str) -> Optional[Dict[str, Any]]:
    """
    Return user's active booking from Google Sheets.
    Handles both Apps Script (array-of-arrays) and gspread (list-of-dicts) responses.
    Returns the original record (keeps original header casing like 'Table_No').
    """
    try:
        sheet_data = get_sheet_data("bookings")
        if not sheet_data:
            print("⚠️ No data returned from bookings sheet.")
            return None

        # Normalize input email
        user_email = normalize_email(email)
        today = datetime.now().date()

        # Convert array-of-arrays -> list-of-dicts if needed
        records = []
        if isinstance(sheet_data, list) and sheet_data and isinstance(sheet_data[0], list):
            # first row = headers, rest = rows
            headers = [h.strip() for h in sheet_data[0]]
            for row in sheet_data[1:]:
                # zip shorter rows safely
                rec = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
                records.append(rec)
        elif isinstance(sheet_data, dict) and "data" in sheet_data and isinstance(sheet_data["data"], list):
            # some Apps Script variants return {"data": [ [...], [...], ... ]}
            rows = sheet_data["data"]
            headers = [h.strip() for h in rows[0]]
            for row in rows[1:]:
                rec = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
                records.append(rec)
        elif isinstance(sheet_data, list) and all(isinstance(r, dict) for r in sheet_data):
            # gspread style already: list of dicts with proper headers
            records = sheet_data
        else:
            # Unknown shape — try to proceed defensively
            print("⚠️ Unrecognized bookings sheet shape; attempting to treat as list of dicts.")
            if isinstance(sheet_data, list):
                records = sheet_data
            else:
                return None

        # Helper: case-insensitive field lookup
        def find_field_value(rec: Dict[str, Any], field_name: str) -> str:
            for k, v in rec.items():
                if str(k).strip().lower() == field_name.lower():
                    return "" if v is None else str(v).strip()
            return ""

        # Search for matching booking
        for rec in records:
            try:
                b_email = normalize_email(find_field_value(rec, "email"))
                if not b_email:
                    continue

                raw_date = find_field_value(rec, "date")
                if not raw_date:
                    # If there's a datetime column with different name (e.g., Ordered_At/Created_At), skip
                    continue

                # Parse date (dateutil parser handles ISO + many formats)
                try:
                    b_date = parser.parse(raw_date).date()
                except Exception as e:
                    print(f"⚠️ Date parse error for {b_email} (raw='{raw_date}'): {e}")
                    continue

                # match email loosely and ensure date is today or in future
                if (b_email == user_email) or (user_email in b_email) or (b_email in user_email):
                    if b_date >= today:
                        print(f"✅ Active booking found for {b_email}: {rec}")
                        return rec  # return original record with original header names

            except Exception as e:
                print(f"⚠️ Error processing record while searching bookings: {e}")
                continue

        print(f"⚠️ No active booking found for {user_email}")
        return None

    except Exception as e:
        print(f"❌ Error in get_active_booking: {e}")
        return None


def create_stripe_checkout(amount_rupees: float, product_name: str) -> Optional[str]:
    """Creates a Stripe Checkout session and returns the URL."""
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": {"name": product_name},
                    "unit_amount": int(amount_rupees * 100) # Convert rupees to paisa (cents)
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=f"{BASE_URL}/success",
            cancel_url=f"{BASE_URL}/cancel"
        )
        return session.url
    except Exception as e:
        print(f"Stripe Error: {e}")
        return None

def clean_name(user_msg: str) -> str:
    """
    Extract and clean user's name from message text.
    Removes phrases like 'my name is', 'this is', 'i am', etc.
    """
    # Try multiple name patterns
    patterns = [
        r"\bmy name is ([A-Za-z ]+)",
        r"\bi am ([A-Za-z ]+)",
        r"\bthis is ([A-Za-z ]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, user_msg, re.IGNORECASE)
        if match:
            name = match.group(1)
            # Remove anything after 'and my email' or 'email is'
            name = re.sub(r"(and my email|email is).*", "", name, flags=re.IGNORECASE)
            return name.strip().title()

    # fallback if no keyword match
    return None


# -------------------------------
# Core Logic: Gemini JSON Handler
# -------------------------------

def process_gemini_json_response(
    json_text: str,
    menu_data: List[Dict[str, str]],
    customer_email: str = "guest@example.com",
    customer_name: str = "Guest User",
) -> Dict[str, Any]:
    """
    Parses Gemini's JSON response, performs the requested action (Order/Booking),
    updates Google Sheets, and returns a natural language confirmation.
    """

    try:
        data = json.loads(json_text)

        # --- Handle Table Booking Intent ---
        if "Booking" in data and isinstance(data["Booking"], dict):
            booking = data["Booking"]

            people = int(booking.get("People", 2))
            date = booking.get("Date") or datetime.now().strftime("%Y-%m-%d")
            time = booking.get("Time") or "8:00 PM"
            name = booking.get("Name", customer_name)
            email = booking.get("Email", customer_email)

            table_no = get_available_table()
            if not table_no:
                return {"response": "⚠️ Sorry, all tables are currently booked. Please try again later."}

            table_type = "4" if people <= 4 else "6"
            amount = 100 * int(table_type)
            payment_link = create_stripe_checkout(amount, f"Table {table_no} for {people}")

            booking_data = {
                "Name": name,
                "Email": normalize_email(email),
                "People": people,
                "Date": date,
                "Time": time,
                "Table_Type": table_type,
                "Table_No": table_no,
                "Payment_Link": payment_link or "N/A",
                "Created_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

            append_to_sheet("bookings", booking_data)

            return {
                "response": f"🪑 Your table for **{people} people** on **{date}** at **{time}** under the name **{name}** has been noted. Please use the payment link below to confirm your reservation.",
                "payment_link": payment_link,
            }

        # --- Handle Food Order Intent ---
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            responses = []

            # ✅ Check if user has an active booking
            active_booking = get_active_booking(customer_email)
            if not active_booking:
                return {"response": "⚠️ You don’t have an active booking. Please book a table first before ordering."}

            table_no = active_booking.get("Table_No", "N/A")

            # ✅ Detect payment mode (cash / online)
            user_msg_lower = json_text.lower()
            payment_mode = None
            if "cash" in user_msg_lower:
                payment_mode = "Cash"
            elif "online" in user_msg_lower or "stripe" in user_msg_lower:
                payment_mode = "Online"

            order_total = 0
            order_items = []

            for item in data:
                dish_name = (item.get("Dish") or "").strip()
                qty = int(item.get("Quantity", 1))
                toppings = (item.get("Toppings") or "").strip()
                notes = (item.get("Notes") or "").strip()

                if not dish_name:
                    responses.append("⚠️ I couldn’t identify the dish name. Please mention it clearly.")
                    continue

                # ✅ Validate dish name against menu
                match = next(
                    (d for d in menu_data if d.get("Dish", "").strip().lower() == dish_name.lower()), None
                )
                if not match:
                    responses.append(f"❌ '{dish_name}' is not on our menu. Please choose a valid dish.")
                    continue

                # ✅ Price calculation
                try:
                    unit_price = float(match.get("Price", 0))
                except (ValueError, TypeError):
                    unit_price = 0.0

                total_price = unit_price * qty
                order_total += total_price

                # ✅ Add row for Google Sheet
                order_data = {
                    "Dish": match.get("Dish", dish_name),
                    "Category": match.get("Category", "Main Course"),
                    "Quantity": qty,
                    "Price": f"₹{unit_price:.0f} × {qty} = ₹{total_price:.0f}",
                    "Time": match.get("Time", "15 min"),
                    "Toppings": toppings if toppings else "None",
                    "Ordered_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "Customer_ID": normalize_email(customer_email),
                    "Customer_Name": customer_name,
                    "Table_No": table_no,
                    "Payment_Mode": payment_mode if payment_mode else "Pending",
                }

                append_to_sheet("orders", order_data)
                order_items.append(order_data)

                t_text = f" with **{toppings}**" if toppings else ""
                n_text = f" (Note: {notes})" if notes else ""
                responses.append(f"✅ {qty} × **{dish_name}**{t_text}{n_text} added to your order! 🍛")

            # ✅ Ask for payment mode if not mentioned
            if not payment_mode:
                return {
                    "response": (
                        "\n".join(responses)
                        + "\n💬 Would you like to pay **online** or by **cash**?"
                    ),
                    "awaiting_payment_mode": True,
                    "order_total": order_total,
                }

            # ✅ If user chose Online payment
            if payment_mode == "Online":
                payment_link = create_stripe_checkout(
                    int(order_total),
                    f"Food Order for {customer_name} ({normalize_email(customer_email)})"
                )
                return {
                    "response": (
                        "\n".join(responses)
                        + f"\n💳 Your total is ₹{order_total:.0f}. Please pay online using this link:\n{payment_link}"
                    ),
                    "payment_link": payment_link,
                }

            # ✅ If user chose Cash payment
            if payment_mode == "Cash":
                return {
                    "response": (
                        "\n".join(responses)
                        + "\n💰 Payment mode set to **Cash**. Please pay at the restaurant."
                    ),
                }

        # --- Handle Fallback Case ---
        return {"response": "⚠️ Sorry, I couldn’t process your request properly. Could you please rephrase?"}

    except json.JSONDecodeError:
        return {"response": json_text}
    except HTTPException as e:
        return {"response": f"⚠️ System error while updating the sheet: {e.detail}"}
    except Exception as e:
        print(f"Error processing Gemini JSON: {e}")
        return {"response": "⚠️ Internal error while processing your request."}


# -------------------------------
# Models (Pydantic)
# -------------------------------
class RegisterRequest(BaseModel):
    Name: str
    Email: str
    Password: str

class LoginRequest(BaseModel):
    Email: str
    Password: str

class BookingRequest(BaseModel):
    Name: str
    Email: str
    People: int
    Date: str
    Time: str
    Table_Type: str = "4"

class ChatRequest(BaseModel):
    message: str
    email: Optional[str] = "guest@example.com"
    name: Optional[str] = "Guest User"


# -------------------------------
# API Routes
# -------------------------------

## Core Status/Health Check
@app.get("/")
def home():
    """Simple health check endpoint."""
    return {"message": "🍛 Fifty Shades Of Gravy Backend is running successfully!"}

## Authentication
@app.post("/register")
async def register_user(req: RegisterRequest):
    """Registers a new user and stores them in the 'users' Google Sheet."""
    Email = normalize_email(req.Email)
    existing = find_user_by_email(Email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    hashed_pw = hash_password(req.Password)
    user_data = {
        "Name": req.Name,
        "Email": Email,
        "Password_Hash": hashed_pw,
        "Created_At": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    append_to_sheet("users", user_data)
    return {"message": "✅ Registration successful", "user": {"Name": req.Name, "Email": Email}}

@app.post("/login")
def login_user(req: LoginRequest):
    """Authenticates a user and returns a JWT token."""
    email = normalize_email(req.Email)
    user = find_user_by_email(email)
    if not user or not verify_password(req.Password, user.get("Password_Hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_jwt(email)
    return {
        "message": "✅ Login successful",
        "token": token,
        "user": {"Name": user.get("Name"), "Email": email}
    }

## Data Routes
@app.get("/menu")
def get_menu():
    """Fetches the full menu from the Google Sheet."""
    data = get_sheet_data("menu")
    return {"menu": data}

@app.post("/book-table")
def book_table(req: BookingRequest):
    """Standard API route for creating a booking (non-chatbot)."""
    amount = 100 * int(req.Table_Type)
    payment_link = create_stripe_checkout(amount, f"Table for {req.People}")
    booking_data = {
        "Name": req.Name,
        "Email": normalize_email(req.Email),
        "People": req.People,
        "Date": req.Date,
        "Time": req.Time,
        "Table_Type": req.Table_Type,
        "Payment_Link": payment_link or "N/A",
        "Created_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    append_to_sheet("bookings", booking_data)
    return {"message": "🪑 Booking created", "payment_link": payment_link}

## The AI Chatbot
from datetime import datetime


user_sessions = {}

@app.post("/chatbot")
def chatbot(req: ChatRequest):
    import re
    from datetime import datetime

    user_msg = req.message.strip()
    user_msg_lower = user_msg.lower()
    session_id = req.email or "guest@example.com"

    # --- Load menu safely ---
    try:
        menu_data = get_sheet_data("menu")
    except Exception:
        return {"response": "⚠️ Menu is currently unavailable. Please try again soon."}

# ====================================================
# 🪑 TABLE BOOKING LOGIC
# ====================================================
    booking_keywords = ["book", "reserve", "reservation", "booking", "table"]
    booking_details_present = any(
        kw in user_msg_lower for kw in ["@", "people", "person", "guest", "-", "am", "pm"]
    )

    # 🔍 Detect if the user is talking about booking a table
    if any(word in user_msg_lower for word in booking_keywords) or booking_details_present:
        # Extract booking details from message
        name = clean_name(user_msg)
        email_match = re.search(r"[\w\.-]+@[\w\.-]+", user_msg_lower)
        people_match = re.search(r"(\d+)\s*(people|person|guests?)", user_msg_lower)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", user_msg_lower)
        time_match = re.search(r"(\d{1,2}:\d{2}\s*(?:am|pm))", user_msg_lower)

        # Build booking data
        booking_data = {
            "Name": name,
            "Email": email_match.group(0) if email_match else None,
            "People": int(people_match.group(1)) if people_match else None,
            "Date": date_match.group(1) if date_match else None,
            "Time": time_match.group(1) if time_match else None,
        }

        # Check if all details are provided
        if all(booking_data.values()):
            try:
                table_data = get_sheet_data("table")
                available_tables = [
                    t for t in table_data if str(t.get("Availability", "")).lower() == "yes"
                ]

                if not available_tables:
                    return {
                        "response": "😔 Sorry, all tables are booked right now. Please try another time."
                    }

                # Assign first available table
                table_no = available_tables[0].get("Table")
                booking_data["Table_No"] = table_no

                # Save booking info
                append_to_sheet("bookings", booking_data)
                append_to_sheet("table", {"Table": table_no, "Availability": "No"})

                # ✅ Save session info
                user_sessions[session_id] = {
                    "email": booking_data["Email"],
                    "name": booking_data["Name"],
                    "table": table_no,
                }

                return {
                    "response": (
                        f"✅ Great! Your table (#{table_no}) for {booking_data['People']} people "
                        f"on {booking_data['Date']} at {booking_data['Time']} is booked, {booking_data['Name']}! "
                        f"A confirmation has been sent to {booking_data['Email']}."
                    )
                }

            except Exception as err:
                print("❌ Booking Save Error:", err)
                return {"response": "⚠️ Booking saved, but issue occurred while updating the sheet."}

        # ❗ Ask for missing details
        missing = [k for k, v in booking_data.items() if not v]
        missing_fields = (
            ", ".join(missing)
            .replace("Email", "email address")
            .replace("People", "number of people")
        )
        return {"response": f"Please share your {missing_fields} to complete your booking."}

# ====================================================
# ❌ CANCEL ORDER LOGIC
# ====================================================
    cancel_keywords = ["cancel", "cancel my order", "remove my order", "delete order", "cancel booking"]

    if any(word in user_msg_lower for word in cancel_keywords):
        try:
            session_data = user_sessions.get(session_id, {})
            user_email = req.email if req.email != "guest@example.com" else session_data.get("email")
            user_name = req.name if req.name != "Guest User" else session_data.get("name", "Guest")

            if not user_email:
                return {"response": "⚠️ Please provide your email so I can process the cancellation request."}

            active_booking = get_active_booking(user_email)
            if not active_booking:
                return {"response": f"⚠️ No active booking or order found for {user_email} to cancel."}

            table_no = active_booking.get("Table_No") or active_booking.get("table_no", "N/A")


        # Log cancellation request for management review
            cancellation_entry = {
            "Customer_Name": user_name,
            "Email": user_email,
            "Table_No": table_no,
            "Request": user_msg,
            "Requested_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Status": "Pending Review",
        }

            append_to_sheet("cancellations", cancellation_entry)

            return {
            "response": (
                f"🛑 Your cancellation request has been sent to our management team, {user_name}. "
                f"They’ll review your order linked to table #{table_no} and confirm shortly."
            )
        }

        except Exception as err:
            print("❌ Cancel Error:", err)
            return {"response": "⚠️ Something went wrong while sending your cancellation request."}



# ====================================================
# 🍽️ MENU / ORDER LOGIC
# ====================================================
    menu_keywords = ["menu", "show me menu", "see menu", "what do you have", "dishes"]
    order_keywords = ["order", "get me", "give me", "add", "serve", "take my order"]

    # --- SHOW MENU ---
    if any(word in user_msg_lower for word in menu_keywords):
        if not menu_data:
            return {"response": "⚠️ Sorry, our menu is currently unavailable."}
        
        # Limit to top 10 dishes for cleaner display
        menu_preview = "\n".join(
            [f"• {m['Dish']} — ₹{m['Price']}" for m in menu_data[:10]]
        )
        return {"response": f"📋 Here's a look at our menu:\n\n{menu_preview}\n\nWould you like to order something?"}

    # --- DETECT ORDER ---
    if any(word in user_msg_lower for word in order_keywords) or re.search(r"\d+\s+[a-zA-Z ]+", user_msg_lower):
        try:
            session_data = user_sessions.get(session_id, {})
            user_email = req.email if req.email != "guest@example.com" else session_data.get("email")
            user_name = req.name if req.name != "Guest User" else session_data.get("name")
            table_no = session_data.get("table")

            if not user_email:
                return {"response": "⚠️ You don’t have an active table booking yet. Please book a table first."}

            active_booking = get_active_booking(user_email)
            if not active_booking:
                print(f"⚠️ No active booking found for {user_email}")
                return {"response": f"⚠️ No active booking found for {user_email}"}

            table_no = active_booking.get("table_no", active_booking.get("Table_No", "N/A"))
            user_name = user_name or active_booking.get("name", active_booking.get("Name", "Guest"))

            # --- Just asking to order, no dish name ---
            if "order" in user_msg_lower and not re.search(r"\d+\s+[a-zA-Z ]+", user_msg_lower):
                return {"response": "🍽️ Sure! What would you like to order today? Try '2 Dal Tadka with extra butter'."}

            # --- Parse multi-dish orders ---
            items = [i.strip() for i in re.split(r",| and ", user_msg_lower) if i.strip()]
            responses = []
            order_list = []
            total_items = 0

            for item in items:
                match_order = re.search(r"(\d+)\s+([a-zA-Z\s]+?)(?: with (.*))?$", item)
                if not match_order:
                    continue

                quantity = int(match_order.group(1))
                dish_name = match_order.group(2).strip().title()
                toppings = match_order.group(3).strip().title() if match_order.group(3) else "None"

    # find the dish in menu
                match = next((m for m in menu_data if m.get("Dish", "").strip().lower() == dish_name.lower()), None)
                if not match:
                    responses.append(f"❌ Sorry, '{dish_name}' isn’t on our menu.")
                    continue

                price = float(match.get("Price", 0))
                category = match.get("Category", "Main Course")
                total_price = price * quantity

                order_data = {
                   "Dish": dish_name,
                   "Category": category,
                   "Quantity": quantity,
                   "Price": f"₹{price:.0f} × {quantity} = ₹{total_price:.0f}",
                   "Toppings": toppings,
                   "Ordered_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "Customer_ID": normalize_email(user_email),
                   "Customer_Name": user_name,
                   "Table_No": table_no,
    }

                append_to_sheet("orders", order_data)
                order_list.append(order_data)
                responses.append(
                    f"✅ Got it! {quantity} × **{dish_name}** (with {toppings}) added to your order. 🍛"
    )
                total_items += 1

            if not responses:
                return {"response": "⚠️ Please mention the dish and quantity like '2 Dal Tadka, 3 Paneer Butter Masala'."}

# ✅ Calculate total amount
            total_amount = sum(float(m.get("Price").replace("₹", "").split("=")[-1].strip()) for m in order_list)

# ✅ Save the last order in session for payment step
            user_sessions[session_id] = user_sessions.get(session_id, {})
            user_sessions[session_id]["last_order"] = order_list
            user_sessions[session_id]["last_order_total"] = total_amount
            print("🧾 Last order stored in session:", user_sessions[session_id].get("last_order"))
            print("💰 Total order amount:", user_sessions[session_id].get("last_order_total"))

            return {
                "response": (
                    "\n".join(responses)
                    + f"\n\nServed soon at your table #{table_no}. 💬 Would you like to pay **Online** or **Cash**?"
           ),
           "awaiting_payment_mode": True,
           "order_data": order_list,
}

            
        except Exception as err:
            print("❌ Order Error:", err)
            return {"response": "⚠️ Something went wrong while placing your order."}

    # ====================================================
# 💳 HANDLE PAYMENT MODE
# ====================================================
    if "online" in user_msg_lower or "cash" in user_msg_lower:
        try:
            session_data = user_sessions.get(session_id, {})
            user_email = req.email if req.email != "guest@example.com" else session_data.get("email")
            active_booking = get_active_booking(user_email)

            if not active_booking:
                return {"response": "⚠️ Please book a table first before selecting a payment mode."}

            table_no = active_booking.get("Table_No", "N/A")
            payment_mode = "Online" if "online" in user_msg_lower else "Cash"

        # ✅ Calculate total from last order stored in session
            last_order = session_data.get("last_order", [])
            order_total = 0

            for item in last_order:
                try:
                    # Extract numeric part from "₹250 × 2 = ₹500"
                    total_part = item["Price"].split("=")[-1].strip().replace("₹", "")
                    order_total += float(total_part)
                except Exception:
                    continue

        # Default to 500 if total not found (fallback)
            if order_total <= 0:
                order_total = 500

        # ✅ Create Stripe link for the real total
            payment_link = None
            if payment_mode == "Online":
                payment_link = create_stripe_checkout(
                    int(order_total),
                    f"Order Payment for {user_email} (Table {table_no})"
                )

            append_to_sheet("orders", {
                  "Customer_ID": normalize_email(user_email),
                  "Table_No": table_no,
                  "Payment_Mode": payment_mode,
             })

            if payment_mode == "Online":
                return {
                   "response": (
                    f"💳 Your total is ₹{order_total:.0f}. Please pay online using this link:\n{payment_link}"
                )
            }
            else:
                return {
                   "response": (
                        f"💰 Payment mode set to **Cash** for total ₹{order_total:.0f}. "
                        "Please pay at the counter after your meal."
                    )
                }

        except Exception as e:
            print("❌ Payment Mode Error:", e)
            return {"response": "⚠️ Something went wrong while updating payment mode."}

# ====================================================
# 📍 LOCATION / CONTACT INFO LOGIC
# ====================================================
    location_keywords = ["location", "where are you", "address", "how to reach", "place", "map","find you"]
    if any(word in user_msg_lower for word in location_keywords):
        return {
        "response": (
            "📍 We’re located at **Fifty Shades of Gravy**, "
            "Koramangala, Bengaluru, near Forum Mall.\n\n"
            "🕒 Open Hours: 11:00 AM – 11:00 PM\n"
            "📞 Contact: +91 98765 43210\n"
            "📬 You can also find us on Google Maps: "
            "[View on Maps](https://goo.gl/maps/abc123)"
        )
    }

# ====================================================
# 💳 STRIPE WEBHOOK HANDLER
# ====================================================
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handles Stripe webhook events to mark payments as completed."""
    try:
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")

        # ✅ Verify webhook signature (secure)
        try:
            event = stripe.Webhook.construct_event(
                payload,
                sig_header,
                STRIPE_SECRET_KEY  # ideally use STRIPE_WEBHOOK_SECRET if you set it
            )
        except stripe.error.SignatureVerificationError as e:
            print("⚠️ Stripe Signature verification failed:", e)
            return {"status": "invalid signature"}

        # ✅ Handle successful payment event
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_details", {}).get("email", "")
            amount_total = session.get("amount_total", 0) / 100

            # Create a record to mark payment complete
            update_entry = {
                "Email": customer_email,
                "Payment_Status": "Completed",
                "Amount_Paid": f"₹{amount_total:.0f}",
                "Updated_At": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

            # Append this info to both sheets
            append_to_sheet("bookings", update_entry)
            append_to_sheet("orders", update_entry)

            print(f"✅ Payment completed for {customer_email}: ₹{amount_total:.0f}")
            return {"status": "success"}

        # ✅ Ignore other event types
        print(f"ℹ️ Ignored event type: {event['type']}")
        return {"status": "ignored"}

    except Exception as e:
        print("❌ Stripe Webhook Error:", e)
        return {"status": "error", "message": str(e)}


        

@app.get("/debug/bookings")
async def debug_bookings(email: str = Query(..., description="User email to check booking")):
    """
    Debug route:
    - Fetches raw bookings data from Google Sheets
    - Checks if an active booking exists for the given email
    """
    try:
        print(f"🔍 Checking active booking for: {email}")

        # 1️⃣ Raw sheet data
        raw_data = get_sheet_data("bookings")

        # 2️⃣ Active booking for the given email
        active_booking = get_active_booking(email)

        # Print for console log debugging
        print("\n--- RAW BOOKINGS DATA ---")
        print(raw_data)
        print("\n--- ACTIVE BOOKING FOUND ---")
        print(active_booking)

        return {
            "email_checked": email,
            "raw_data_type": type(raw_data).__name__,
            "total_rows": len(raw_data) if isinstance(raw_data, list) else "unknown",
            "sample_first_row": raw_data[0] if isinstance(raw_data, list) and raw_data else None,
            "active_booking": active_booking or "⚠️ No active booking found",
        }

    except Exception as e:
        print(f"❌ Error in /debug/bookings: {e}")
        return {"error": str(e)}

# ====================================================
# 💬 DEFAULT CHAT RESPONSE
# ====================================================
    return {"response": "👋 Hi there! I can help you book a table or place an order. What would you like to do?"}
