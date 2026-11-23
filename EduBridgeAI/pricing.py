"""Location-based price *estimates* for tutors.

IMPORTANT: these are heuristic estimates built from the rate tables below. They are NOT live market
data. Edit the tables to match real rates you collect. The UI always labels them "estimated" and never
changes a tutor's saved price.
"""
from math import radians, sin, cos, asin, sqrt

CATEGORIES = {
    "School subjects (Class 1-10)": 300,
    "Higher secondary / Intermediate (11-12)": 400,
    "Competitive exams (JEE, NEET, UPSC, GATE, CAT)": 550,
    "Programming & technology": 450,
    "Data, AI & analytics": 500,
    "Languages & spoken English": 300,
    "Music, arts & hobbies": 350,
    "College / university subjects": 450,
    "Other": 300,
}

# Keywords that raise/lower local demand for a subject (multiplier on the category base rate).
DEMAND = {
    "jee": 1.25, "neet": 1.25, "upsc": 1.2, "gate": 1.15, "cat": 1.15, "ielts": 1.15, "gre": 1.15,
    "python": 1.15, "machine learning": 1.3, "data science": 1.25, "ai": 1.2, "java": 1.1,
    "web development": 1.1, "sql": 1.1, "maths": 1.05, "mathematics": 1.05, "physics": 1.05,
    "chemistry": 1.05, "spoken english": 0.95, "drawing": 0.9, "painting": 0.9,
}

# City coordinates (lat, lon) and tier. Tier 1 = largest metros. Extend as needed.
CITIES = {
    "mumbai": (19.076, 72.8777, 1, "Maharashtra"), "delhi": (28.6139, 77.209, 1, "Delhi"),
    "new delhi": (28.6139, 77.209, 1, "Delhi"), "bengaluru": (12.9716, 77.5946, 1, "Karnataka"),
    "bangalore": (12.9716, 77.5946, 1, "Karnataka"), "hyderabad": (17.385, 78.4867, 1, "Telangana"),
    "chennai": (13.0827, 80.2707, 1, "Tamil Nadu"), "pune": (18.5204, 73.8567, 1, "Maharashtra"),
    "kolkata": (22.5726, 88.3639, 1, "West Bengal"), "gurugram": (28.4595, 77.0266, 1, "Haryana"),
    "noida": (28.5355, 77.391, 1, "Uttar Pradesh"), "secunderabad": (17.4399, 78.4983, 1, "Telangana"),
    "ahmedabad": (23.0225, 72.5714, 2, "Gujarat"), "jaipur": (26.9124, 75.7873, 2, "Rajasthan"),
    "lucknow": (26.8467, 80.9462, 2, "Uttar Pradesh"), "chandigarh": (30.7333, 76.7794, 2, "Chandigarh"),
    "kochi": (9.9312, 76.2673, 2, "Kerala"), "thiruvananthapuram": (8.5241, 76.9366, 2, "Kerala"),
    "coimbatore": (11.0168, 76.9558, 2, "Tamil Nadu"), "visakhapatnam": (17.6868, 83.2185, 2, "Andhra Pradesh"),
    "vijayawada": (16.5062, 80.648, 2, "Andhra Pradesh"), "guntur": (16.3067, 80.4365, 2, "Andhra Pradesh"),
    "tirupati": (13.6288, 79.4192, 2, "Andhra Pradesh"), "warangal": (17.9689, 79.5941, 2, "Telangana"),
    "karimnagar": (18.4386, 79.1288, 3, "Telangana"), "nizamabad": (18.6725, 78.0941, 3, "Telangana"),
    "nagpur": (21.1458, 79.0882, 2, "Maharashtra"), "indore": (22.7196, 75.8577, 2, "Madhya Pradesh"),
    "bhopal": (23.2599, 77.4126, 2, "Madhya Pradesh"), "patna": (25.5941, 85.1376, 2, "Bihar"),
    "bhubaneswar": (20.2961, 85.8245, 2, "Odisha"), "mysuru": (12.2958, 76.6394, 2, "Karnataka"),
    "mysore": (12.2958, 76.6394, 2, "Karnataka"), "surat": (21.1702, 72.8311, 2, "Gujarat"),
    "vadodara": (22.3072, 73.1812, 2, "Gujarat"), "madurai": (9.9252, 78.1198, 2, "Tamil Nadu"),
    "mangaluru": (12.9141, 74.856, 2, "Karnataka"), "guwahati": (26.1445, 91.7362, 2, "Assam"),
    "dehradun": (30.3165, 78.0322, 2, "Uttarakhand"), "ranchi": (23.3441, 85.3096, 2, "Jharkhand"),
}
TIER_FACTOR = {1: 1.25, 2: 1.0, 3: 0.85}
UNLISTED_CITY_FACTOR = 0.8

# Purchasing-power multipliers vs. India. Output is still shown in INR (the marketplace bills in INR only).
COUNTRY_FACTOR = {"india": 1.0, "united states": 4.0, "usa": 4.0, "united kingdom": 3.5, "uk": 3.5,
                  "united arab emirates": 2.5, "uae": 2.5, "singapore": 3.5, "australia": 3.5, "canada": 3.5}

ONLINE_FACTOR = 0.85          # online sessions are typically cheaper (no travel)
FREE_TRAVEL_KM = 3.0
TRAVEL_RATE_PER_KM = 8        # INR per km beyond the free radius, per session (one way)
WEEKLY_DISCOUNT = 0.95
MONTHLY_DISCOUNT = 0.90
QUAL_KEYWORDS = [("phd", 1.25), ("doctorate", 1.25), ("m.tech", 1.12), ("m.sc", 1.12), ("mba", 1.12),
                 ("master", 1.12), ("m.ed", 1.12), ("b.ed", 1.06), ("b.tech", 1.05), ("b.sc", 1.05),
                 ("bachelor", 1.05), ("certified", 1.05), ("certification", 1.05)]


def _key(s):
    return (s or "").strip().lower()


def city_info(city):
    return CITIES.get(_key(city))


def distance_km(city_a, city_b):
    """Great-circle distance between two *known* cities, or None when either city is not in the table."""
    a, b = city_info(city_a), city_info(city_b)
    if not a or not b:
        return None
    if _key(city_a) == _key(city_b):
        return 0.0
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return round(2 * 6371 * asin(sqrt(h)), 1)


def qualification_factor(text):
    t = _key(text)
    best = 1.0
    for kw, f in QUAL_KEYWORDS:
        if kw in t:
            best = max(best, f)
    return best


def demand_factor(subject_text):
    t = _key(subject_text)
    best = 1.0
    for kw, f in DEMAND.items():
        if kw in t and abs(f - 1) > abs(best - 1):
            best = f
    return best


def estimate_hourly(category, subjects, city, country, years, qualifications, mode,
                    student_city=None):
    """Return a dict with an estimated hourly range and a transparent breakdown.

    mode: 'online' or 'offline'. Travel surcharge only applies to offline + known distance.
    """
    base = CATEGORIES.get(category, CATEGORIES["Other"])
    country_f = COUNTRY_FACTOR.get(_key(country) or "india", 1.0)
    ci = city_info(city)
    city_f = TIER_FACTOR[ci[2]] if ci else UNLISTED_CITY_FACTOR
    if _key(country) not in ("", "india"):
        city_f = 1.0  # tier table only covers India
    exp_f = 1 + min(max(int(years or 0), 0), 15) * 0.03
    qual_f = qualification_factor(qualifications)
    dem_f = demand_factor(f"{category} {subjects}")
    mode_f = ONLINE_FACTOR if mode == "online" else 1.0
    mid = base * country_f * city_f * exp_f * qual_f * dem_f * mode_f
    travel = None
    dist = None
    if mode == "offline" and student_city:
        dist = distance_km(city, student_city)
        if dist is not None:
            travel = round(max(0.0, dist - FREE_TRAVEL_KM) * TRAVEL_RATE_PER_KM)
    return {
        "low": int(round(mid * 0.85, -1)), "mid": int(round(mid, -1)), "high": int(round(mid * 1.2, -1)),
        "distance_km": dist, "travel_fee": travel,
        "factors": {"base rate for category": base, "country": country_f, "city tier": city_f,
                    "experience": round(exp_f, 2), "qualifications": qual_f, "subject demand": dem_f,
                    "online/offline": mode_f},
        "note": ("Estimate from built-in rate tables, not live market data. "
                 + ("City not in the built-in city table, so a conservative local factor was used. "
                    if not ci and _key(country) in ("", "india") else "")
                 + ("Non-India results are shown in INR; billing in other currencies is not supported."
                    if _key(country) not in ("", "india") else "")).strip(),
    }


def package_estimate(hourly, sessions, hours_per_session, plan):
    """Estimated weekly/monthly price from an hourly price (used for suggestions only)."""
    total_hours = sessions * hours_per_session
    disc = WEEKLY_DISCOUNT if plan == "weekly" else MONTHLY_DISCOUNT
    return int(round(hourly * total_hours * disc, -1))


def compare_to_market(price, low, high):
    """Classify an actual tutor price against an estimated range."""
    if not price:
        return "not set"
    if price < low:
        return "below estimated range"
    if price > high:
        return "above estimated range"
    return "within estimated range"
