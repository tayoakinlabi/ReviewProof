"""Alert routing (step 2.5)."""

import json
import smtplib
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from reviewproof import alerts
from reviewproof.config import AlertRoute, Smtp
from reviewproof.metrics.checks import Check, State

NOW = datetime(2026, 3, 6, 12, tzinfo=timezone.utc)
BREACH = Check("catch_rate", "Planted errors caught", State.BREACH, "58% against 80%.")


class Receiver(ThreadingHTTPServer):
    """A local stand-in for the QA lead's webhook."""

    def __init__(self, status=200, location=None):
        self.requests = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.requests.append((self.path, dict(self.headers), body))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *args):
                pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}/hook?token=SECRET-TOKEN"

    def stop(self):
        self.shutdown()
        self.server_close()


@pytest.fixture
def serve():
    servers = []

    def start(**arguments):
        servers.append(Receiver(**arguments))
        return servers[-1]

    yield start
    for server in servers:
        server.stop()


pytestmark = pytest.mark.usefixtures("no_proxy")


def message(*checks):
    return alerts.compose("deviation-triage", list(checks or [BREACH]), NOW)


@pytest.mark.req("REQ-040")
def test_the_message_is_about_the_queue():
    result = message()
    assert result.subject == ("ReviewProof: 1 item needs attention in queue deviation-triage")
    assert "58% against 80%." in result.body
    assert result.payload["alerts"] == [
        {"rule": "catch_rate", "title": "Planted errors caught", "detail": "58% against 80%."}
    ]
    assert result.payload["generated_at"] == "2026-03-06T12:00:00.000000Z"
    assert "2 items need attention" in message(BREACH, BREACH).subject


def test_a_subject_cannot_carry_new_headers():
    hostile = alerts.compose("q\r\nBcc: x@y.com", [BREACH], NOW)
    assert "\n" not in hostile.subject and "\r" not in hostile.subject


def test_a_webhook_receives_one_post(serve):
    server = serve()
    assert alerts.send(message(), AlertRoute(webhook=server.url)) == ["webhook"]
    [(path, headers, body)] = server.requests
    assert path == "/hook?token=SECRET-TOKEN"
    assert json.loads(body)["queue"] == "deviation-triage"
    assert headers["User-Agent"] == "reviewproof"


@pytest.mark.parametrize("status", [400, 500])
def test_a_refusing_webhook_is_an_error_that_hides_the_address(serve, status):
    server = serve(status=status)
    with pytest.raises(alerts.DeliveryError, match=f"HTTP {status}") as error:
        alerts.send(message(), AlertRoute(webhook=server.url))
    assert "SECRET-TOKEN" not in str(error.value)


def test_redirects_are_not_followed(serve):
    elsewhere = serve()
    server = serve(status=302, location=elsewhere.url)
    with pytest.raises(alerts.DeliveryError, match="302"):
        alerts.send(message(), AlertRoute(webhook=server.url))
    assert elsewhere.requests == []


def test_an_unreachable_webhook():
    route = AlertRoute(webhook="http://127.0.0.1:9/SECRET-TOKEN")
    with pytest.raises(alerts.DeliveryError) as error:
        alerts.send(message(), route)
    assert "SECRET-TOKEN" not in str(error.value)


class FakeSmtp:
    sent = []

    def __init__(self, host, port, timeout):
        self.log = [("connect", host, port)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        FakeSmtp.sent.append(self)

    def starttls(self, context):
        assert context.check_hostname
        self.log.append(("starttls",))

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, email):
        self.email = email


@pytest.fixture
def smtp():
    FakeSmtp.sent = []
    return FakeSmtp


SMTP = Smtp(host="mail.example.com", sender="rp@example.com")


@pytest.mark.req("REQ-040")
def test_email_to_the_qa_lead(smtp):
    route = AlertRoute(emails=("qa@example.com", "qa2@example.com"), smtp=SMTP)
    assert alerts.send(message(), route, smtp) == ["email to 2 address(es)"]
    [server] = smtp.sent
    assert server.email["To"] == "qa@example.com, qa2@example.com"
    assert server.email["From"] == "rp@example.com"
    assert "58% against 80%." in server.email.get_content()
    assert [step[0] for step in server.log] == ["connect", "starttls"]


def test_credentials_come_from_the_environment(smtp):
    route = AlertRoute(
        emails=("qa@example.com",),
        smtp=Smtp("h", "rp@example.com", username_env="RP_USER", password_env="RP_PASS"),
    )
    alerts.send(message(), route, smtp, environment={"RP_USER": "u", "RP_PASS": "p"})
    assert smtp.sent[0].log[-1] == ("login", "u", "p")
    with pytest.raises(alerts.DeliveryError, match="RP_PASS"):
        alerts.send(message(), route, smtp, environment={"RP_USER": "u"})


def test_plain_smtp_without_starttls(smtp):
    route = AlertRoute(emails=("qa@example.com",), smtp=Smtp("h", "a@b.com", starttls=False))
    alerts.send(message(), route, smtp)
    assert [step[0] for step in smtp.sent[0].log] == ["connect"]


def test_every_route_is_tried_even_if_one_fails(serve):
    server = serve()

    def broken(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "busy")

    route = AlertRoute(emails=("qa@example.com",), webhook=server.url, smtp=SMTP)
    with pytest.raises(alerts.DeliveryError, match="email"):
        alerts.send(message(), route, broken)
    assert len(server.requests) == 1


@pytest.mark.req("REQ-043")
def test_without_a_route_nothing_is_sent():
    with pytest.raises(alerts.DeliveryError, match="no alert route"):
        alerts.send(message(), AlertRoute())
