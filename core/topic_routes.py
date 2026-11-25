"""Flask integration for the local desktop topic radar."""
from urllib.parse import urlparse
from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException
from . import store, topic_radar as radar, topic_ai as ai, topic_profile as positioning
from . import profile_bootstrap as bootstrap
from . import topic_research as research

blueprint = Blueprint("topic_radar", __name__, url_prefix="/api/topic-radar")


@blueprint.before_request
def local_write_guard():
    if urlparse(request.host_url).hostname not in ("127.0.0.1", "localhost", "::1"):
        return jsonify(error="选题功能仅接受本机访问"), 403
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error="不接受其他网页发起的选题修改"), 403
        limit = 512 * 1024 if request.endpoint == "topic_radar.profile_works" else 65536
        if not request.is_json or request.content_length and request.content_length > limit:
            return jsonify(error=f"请求需为不超过 {limit // 1024}KB 的 JSON"), 400


@blueprint.errorhandler(radar.Conflict)
def conflict(error):
    return jsonify(error=str(error)), 409


@blueprint.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@blueprint.errorhandler(ai.AIError)
def ai_failure(error):
    return jsonify(error=str(error)), 502


@blueprint.errorhandler(Exception)
def unexpected_failure(error):
    if isinstance(error, HTTPException):
        return jsonify(error="选题接口请求无效，请刷新页面后重试"), error.code or 500
    current_app.logger.exception("选题接口异常：%s", request.path)
    return jsonify(error="选题服务暂时异常，已保存的选题会保留。请重试；若刚更新程序，请完全退出后重新启动。"), 500


@blueprint.route("/ai/config", methods=["GET", "PUT"])
def ai_config():
    return jsonify(ai.config() if request.method == "GET" else ai.save_config(request.get_json()))


@blueprint.post("/ai/test")
def ai_test():
    return jsonify(ai.test_connection(request.get_json()))


@blueprint.get("/ai/providers")
def ai_providers():
    return jsonify(ai.providers())


@blueprint.put("/ai/providers/<provider_id>")
def ai_save_provider(provider_id):
    return jsonify(ai.save_provider(provider_id, request.get_json()))


@blueprint.post("/ai/providers/<provider_id>/test")
def ai_test_provider(provider_id):
    return jsonify(ai.test_provider(provider_id, request.get_json()))


@blueprint.route("/search/config", methods=["GET", "PUT"])
def search_config():
    return jsonify(research.config() if request.method == "GET" else research.save_config(request.get_json()))


@blueprint.post("/search/test")
def search_test():
    return jsonify(research.test_connection())


@blueprint.post("/accounts/<account_id>/topics/<topic_id>/research")
def research_topic(account_id, topic_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(research.research_topic(account, topic_id, request.get_json()))


@blueprint.get("/accounts/<account_id>/topics/<topic_id>")
def get_topic(account_id, topic_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(research.get_topic(account, topic_id))


@blueprint.route("/accounts/<account_id>/ai/recommendations", methods=["GET", "POST"])
def ai_recommendations(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(ai.recommendations(account, generate=request.method == "POST"))


@blueprint.post("/accounts/<account_id>/topics/<topic_id>/ai")
def ai_generate(account_id, topic_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(ai.generate_topic(account, topic_id, request.get_json()))


def account_or_none(account_id):
    return store.get_account(account_id)


@blueprint.route("/accounts/<account_id>/profile/works", methods=["GET", "PUT"])
def profile_works(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(positioning.dataset(account) if request.method == "GET" else positioning.import_works(account, request.get_json()))


@blueprint.route("/accounts/<account_id>/profile/bootstrap", methods=["GET", "POST"])
def profile_bootstrap(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(bootstrap.state(account) if request.method == "GET" else bootstrap.command(account, request.get_json()))


@blueprint.route("/accounts/<account_id>/profile/analysis", methods=["GET", "POST"])
def profile_analysis(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(positioning.analysis(account) if request.method == "GET" else positioning.analyze(account, request.get_json()))


@blueprint.post("/accounts/<account_id>/profile/adopt")
def profile_adopt(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(positioning.adopt(account, request.get_json()))


@blueprint.get("/status")
def status():
    return jsonify(radar.status())


@blueprint.get("/accounts")
def accounts():
    return jsonify([{**{key: account.get(key) for key in ("id", "name", "platform", "tags")}, "profile": radar.get_profile(account)} for account in store.list_accounts()])


@blueprint.route("/accounts/<account_id>/profile", methods=["GET", "PUT"])
def profile(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(radar.get_profile(account) if request.method == "GET" else radar.save_profile(account, request.get_json()))


@blueprint.get("/accounts/<account_id>/feed")
def feed(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    lane = request.args.get("lane", "matched")
    if lane not in ("matched", "rising", "all", "douyin"):
        raise ValueError("未知的热点列表")
    return jsonify(radar.feed(account, lane))


@blueprint.post("/refresh")
def refresh():
    started = radar.start_refresh()
    return jsonify(started=started, refresh=radar.status()["refresh"]), 202


@blueprint.put("/settings")
def settings():
    return jsonify(radar.save_settings(request.get_json()))


@blueprint.route("/accounts/<account_id>/topics", methods=["GET", "POST"])
def topics(account_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    if request.method == "GET":
        return jsonify(radar.list_topics(account))
    payload = request.get_json()
    if not isinstance(payload, dict) or not isinstance(payload.get("lead_id"), str) or not isinstance(payload.get("style"), str) or payload["style"] not in radar.STYLES:
        raise ValueError("选题参数不正确")
    return jsonify(radar.save_topic(account, payload["lead_id"], payload["style"])), 201


@blueprint.patch("/accounts/<account_id>/topics/<topic_id>")
def update_topic(account_id, topic_id):
    account = account_or_none(account_id)
    if not account:
        return jsonify(error="账号已删除或不存在"), 404
    return jsonify(radar.update_topic(account, topic_id, request.get_json()))


def init_app(app):
    app.register_blueprint(blueprint)

    @app.before_request
    def monitor_when_running():
        if not current_app.testing:
            radar.ensure_monitor()
