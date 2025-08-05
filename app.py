from flask import Flask, request, jsonify
import json
import re
import parsedatetime
import ollama
from datetime import datetime, timedelta
import dateutil.parser

app = Flask(__name__)

def build_prompt(query):
    return f"""
You are a flight booking assistant.

Extract and return only JSON with the following keys:
- from: departure city
- to: arrival city
- depdate: departure date (natural format like "after 5 days" allowed)
- retdate: return date (optional)
- adults: number of adults (default: 1)
- children: number of children (default: 0)
- infants: number of infants (default: 0)
- cabin: cabin class like economy, business (default: economy)
- airline_include: preferred airline if mentioned (e.g., "by Indigo")

⚠️ Only assign a value if it is clearly mentioned in the query. 
If a field is missing, set its value to null or "Not Provided",except:
- Set "adults" to 1 by default
- Set "children" and "infants" to 0 by default
- Set "cabin" to "economy" by default

Return valid JSON only. Do not explain anything.

Query: "{query}"
"""


def parse_date_string(natural_date, base_date=None):
    if not natural_date:
        return None

    natural_date_lower = natural_date.lower().strip()
    today = base_date or datetime.now()

    # Handle common phrases
    if "day after tomorrow" in natural_date_lower:
        return (today + timedelta(days=2)).strftime('%Y-%m-%d')
    if "tomorrow" in natural_date_lower:
        return (today + timedelta(days=1)).strftime('%Y-%m-%d')
    match = re.search(r'after (\d+) days?', natural_date_lower)
    if match:
        days = int(match.group(1))
        return (today + timedelta(days=days)).strftime('%Y-%m-%d')

    # parsedatetime
    cal = parsedatetime.Calendar()
    time_struct, parse_status = cal.parse(natural_date, sourceTime=today.timetuple())
    if parse_status != 0:
        return datetime(*time_struct[:6]).strftime('%Y-%m-%d')

    # fallback
    try:
        return dateutil.parser.parse(natural_date, fuzzy=True, default=today).strftime('%Y-%m-%d')
    except Exception:
        return None

def is_missing(value):
    if value is None:
        return True
    value = str(value).strip().lower()
    return value in ["", "none", "not provided", "departure city (not provided)", "arrival city (not provided)"]

@app.route('/parse', methods=['POST'])
def parse_query():
    try:
        data = request.get_json()
        user_query = data.get("query")
        if not user_query:
            return jsonify({"error": "Missing 'query'"}), 400

        prompt = build_prompt(user_query)
        response = ollama.chat(model='mistral', messages=[
            {"role": "user", "content": prompt}
        ])
        content = response['message']['content'].strip()
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return jsonify({"error": "Invalid JSON in model output"}), 500

        parsed = json.loads(json_match.group())

        depdate_raw = parsed.get("depdate")
        retdate_raw = parsed.get("retdate")
        depfrom = parsed.get("from")
        arrto = parsed.get("to")

        # Force missing if not clearly mentioned
        if depfrom and "from" not in user_query.lower():
            depfrom = None
        if arrto and "to" not in user_query.lower():
            arrto = None

        depdate = parse_date_string(depdate_raw) if depdate_raw else None
        retdate = parse_date_string(retdate_raw, base_date=datetime.strptime(depdate, "%Y-%m-%d")) if retdate_raw and depdate else None


        adults = int(parsed.get("adults", 1))
        children = int(parsed.get("children", 0))
        infants = int(parsed.get("infants", 0))
        cabin = parsed.get("cabin", "economy").lower()
        airline = parsed.get("airline_include", "")

        # Check for missing fields
        missing_fields = []
        follow_up_questions = []

        if is_missing(depfrom):
            missing_fields.append("from")
            follow_up_questions.append("✈️ Where are you flying *from*?")
        if is_missing(arrto):
            missing_fields.append("to")
            follow_up_questions.append("🛬 Where are you flying *to*?")
        if not depdate_raw or not depdate:
            missing_fields.append("depdate")
            follow_up_questions.append("📅 When do you want to *depart*?")

        if missing_fields:
            return jsonify({
                "status": "incomplete",
                "message": f"Missing fields: {', '.join(missing_fields)}",
                "missing_fields": missing_fields,
                "follow_up": follow_up_questions,
                "parsed": {
                    "from": depfrom,
                    "to": arrto,
                    "depdate": depdate_raw or None
                }
            })

        # Final payload
        payload = {
            "adults": adults,
            "children": children,
            "infants": infants,
            "cabin": cabin,
            "stops": False,
            "airline_include": airline,
            "ages": [],
            "segments": [
                {
                    "depfrom": depfrom,
                    "arrto": arrto,
                    "depdate": depdate
                }
            ]
        }

        if retdate:
            payload["segments"].append({
                "depfrom": arrto,
                "arrto": depfrom,
                "depdate": retdate
            })

        return jsonify({
            "status": "complete",
            "payload": payload
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
