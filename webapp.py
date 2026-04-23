import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError

from flask import Flask, request, abort, render_template, jsonify

from config import WEB_TOKEN, GUILD_ID, BLACKLIST_FILE
from storage import load_json


def create_app(bot):
    app = Flask(__name__, template_folder="templates")

    def auth_ok():
        if not WEB_TOKEN:
            return True
        return request.args.get("token", "") == WEB_TOKEN

    async def fetch_bans_payload():
        guild = bot.get_guild(GUILD_ID)
        file_blacklist = load_json(BLACKLIST_FILE, {"banned_ids": []})

        if guild is None:
            return {
                "guild_found": False,
                "bans": [],
                "blacklist_ids": file_blacklist["banned_ids"],
            }

        bans = []
        try:
            async for ban_entry in guild.bans(limit=None):
                bans.append(
                    {
                        "id": ban_entry.user.id,
                        "name": str(ban_entry.user),
                        "reason": ban_entry.reason or "",
                    }
                )
        except Exception:
            pass

        bans.sort(key=lambda x: str(x["name"]).lower())

        return {
            "guild_found": True,
            "bans": bans,
            "blacklist_ids": file_blacklist["banned_ids"],
        }

    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/health")
    def health():
        return {"status": "ok"}, 200

    @app.route("/bans")
    def bans_page():
        if not auth_ok():
            abort(403)
        return render_template("bans.html")

    @app.route("/api/bans")
    def api_bans():
        if not auth_ok():
            abort(403)

        try:
            future = asyncio.run_coroutine_threadsafe(fetch_bans_payload(), bot.loop)
            payload = future.result(timeout=10)
            return jsonify(payload)
        except FuturesTimeoutError:
            return jsonify({"error": "timeout"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app
