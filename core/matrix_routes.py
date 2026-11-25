"""矩媒连接、账号映射与只读发布记录。"""
from flask import Blueprint, jsonify, request
from . import matrixmedia


def init_app(app):
    routes = Blueprint("matrixmedia", __name__, url_prefix="/api/matrixmedia")

    @routes.get("/settings")
    def settings():
        return jsonify(matrixmedia.settings())

    @routes.post("/settings")
    def save_settings():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            return jsonify(error="矩媒配置格式不正确"), 400
        try:
            return jsonify(matrixmedia.save_settings(value))
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @routes.get("/probe")
    def probe():
        try:
            return jsonify(matrixmedia.probe())
        except RuntimeError as error:
            return jsonify(error=str(error)), 503

    @routes.post("/open")
    def open_app():
        try:
            return jsonify(matrixmedia.open_app())
        except (ValueError, OSError) as error:
            return jsonify(error=str(error)), 400

    @routes.get("/history")
    def history():
        try:
            rows = matrixmedia.read_records("pushData")
            # 独立展示矩媒返回值，不用任务已入队推断平台已发布。
            allowed = ("id", "pt", "partition", "phone", "status", "message", "createTime",
                       "publishAt", "scheduledPublishAtText", "bookName", "publishStatus")
            return jsonify(records=[{**{k: row[k] for k in allowed if k in row},
                "title": str((row["data"] if isinstance(row.get("data"), dict) else {}).get("title") or row.get("textOtherName") or "")}
                for row in rows[-200:]])
        except RuntimeError as error:
            return jsonify(error=str(error)), 503

    app.register_blueprint(routes)
