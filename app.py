from flask import Flask, jsonify, redirect, render_template, send_from_directory
from dotenv import load_dotenv
import os
import webbrowser

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

@app.route("/config")
def get_config():
    return jsonify({
        "GOOGLE_MAPS_API_KEY": os.getenv("GOOGLE_MAPS_API_KEY"),
        "OPENWEATHER_API_KEY": os.getenv("OPENWEATHER_API_KEY")
    })

@app.route("/")
def home():
    return redirect("/map")

@app.route("/map")
def map_page():
    return render_template("map.html")

if __name__ == "__main__":
    url = "http://127.0.0.1:5000/map"
    print(f"Server running at {url}")
    webbrowser.open(url)
    app.run(debug=True)
