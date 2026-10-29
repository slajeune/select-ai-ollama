
import json
import os
import logging
from datetime import date, datetime
from decimal import Decimal

import oracledb
from dotenv import load_dotenv
from flask import Flask, render_template, request

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

PROFILE_NAME = os.environ["SELECT_AI_PROFILE"]


def normalize_value(value):
    if value is None:
        return ""

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    return value


def parse_search_results(raw_value):
    """
    Convert Select AI's JSON array response into rows and columns.
    Handles a CLOB, a JSON string, or an already-decoded list.
    """
    if raw_value is None:
        return [], []

    if hasattr(raw_value, "read"):
        raw_value = raw_value.read()

    if isinstance(raw_value, str):
        text = raw_value.strip()

        # Handle an optional label such as "Search result: [...]".
        if text.lower().startswith("search result:"):
            text = text.split(":", 1)[1].strip()

        # Handle JSON wrapped in Markdown code fences.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        raw_value = json.loads(text)

    # Some responses may wrap the array in an object.
    if isinstance(raw_value, dict):
        for key in ("results", "rows", "items", "data"):
            if isinstance(raw_value.get(key), list):
                raw_value = raw_value[key]
                break
        else:
            raw_value = [raw_value]

    if not isinstance(raw_value, list):
        raise ValueError("Select AI did not return a JSON array.")

    # Keep only object rows, preserving the original column order.
    rows = [item for item in raw_value if isinstance(item, dict)]

    if not rows:
        return [], []

    columns = list(dict.fromkeys(
        key for row in rows for key in row.keys()
    ))

    normalized_rows = [
        {column: normalize_value(row.get(column)) for column in columns}
        for row in rows
    ]

    return columns, normalized_rows


def search_oracle(question):
    connection = oracledb.connect(
        user=os.environ["SELECT_AI_USER"],
        password=os.environ["SELECT_AI_PASSWORD"],
        dsn=os.environ["SELECT_AI_DB_CONNECT_STRING"],
    )

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DBMS_CLOUD_AI.GENERATE(
                    prompt       => :prompt,
                    profile_name => :profile_name,
                    action       => 'runsql'
                )
                FROM dual
                """,
                prompt=question,
                profile_name=PROFILE_NAME,
            )

            raw_value = cursor.fetchone()[0]

            columns, rows = parse_search_results(raw_value)
            return columns, rows

    finally:
        connection.close()


@app.route("/", methods=["GET", "POST"])
def index():
    question = ""
    columns = []
    results = []
    error = None

    if request.method == "POST":
        question = request.form.get("q", "").strip()

        if not question:
            error = "Enter a search query."
        elif len(question) > 500:
            error = "Search queries must be 500 characters or fewer."
        else:
            try:
                columns, results = search_oracle(question)
            except Exception:
                app.logger.exception("Oracle Select AI search failed")
                error = (
                    "Search failed. Check the Flask server logs "
                    "for details."
                )

    return render_template(
        "index.html",
        question=question,
        query=question,
        columns=columns,
        results=results,
        error=error,
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

