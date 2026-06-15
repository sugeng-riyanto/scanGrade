"""Locust load test — ScanGrade 500 concurrent students.

This simulates realistic student behavior:
- Login as student (demo accounts)
- Browse exam list
- Take an exam (view questions)
- Submit exam with dummy answers
- View results

Run from your LOCAL machine (not the VPS):

  pip install locust
  locust -f locustfile.py --host=https://scangrade.web.id --users=500 --spawn-rate=50 --run-time=10m --headless --csv=loadtest

Or with web UI:

  locust -f locustfile.py --host=https://scangrade.web.id
"""
import json
import random
import re
from locust import HttpUser, task, between


# Demo accounts: murid1@scan-grade.app .. murid300@scan-grade.app, password: demo123
STUDENT_EMAILS = [f"murid{i}@scan-grade.app" for i in range(1, 301)]


class ScanGradeStudent(HttpUser):
    wait_time = between(2, 8)

    def on_start(self):
        """Login as a random demo student."""
        self.email = random.choice(STUDENT_EMAILS)
        self.password = "demo123"
        self.logged_in = False
        self.exam_ids = []

        resp = self.client.post(
            "/auth/login",
            json={"email": self.email, "password": self.password},
            name="POST /auth/login (login)",
        )
        if resp.status_code == 200 or resp.status_code == 302:
            self.logged_in = True
        else:
            # Try demo endpoint
            resp2 = self.client.post(
                "/auth/demo-login",
                json={"role": "murid"},
                name="POST /auth/demo-login",
            )
            if resp2.status_code == 200 or resp2.status_code == 302:
                self.logged_in = True

    @task(3)
    def view_dashboard(self):
        """GET /student/dashboard — main student dashboard."""
        if not self.logged_in:
            return
        self.client.get("/student/dashboard", name="GET /student/dashboard")

    @task(5)
    def view_exams(self):
        """GET /student/exams — list available exams."""
        if not self.logged_in:
            return
        resp = self.client.get("/student/exams", name="GET /student/exams")
        if resp.status_code == 200:
            # Extract exam IDs from response for subsequent tasks
            ids = re.findall(r'/student/exams/([a-f0-9\-]{36})', resp.text)
            if ids:
                self.exam_ids = list(set(ids))

    @task(3)
    def view_exam_detail(self):
        """GET /student/exams/<id> — open an exam (question page)."""
        if not self.logged_in or not self.exam_ids:
            return
        exam_id = random.choice(self.exam_ids)
        self.client.get(
            f"/student/exams/{exam_id}",
            name="GET /student/exams/[id]",
        )

    @task(1)
    def view_results(self):
        """GET /student/results — student's past results."""
        if not self.logged_in:
            return
        self.client.get("/student/results", name="GET /student/results")

    @task(1)
    def view_result_detail(self):
        """GET /student/results/<id> — open a result detail (if any)."""
        if not self.logged_in:
            return
        resp = self.client.get("/student/results", name="GET /student/results (list)")
        if resp.status_code == 200:
            ids = re.findall(r'/student/results/([a-f0-9\-]{36})', resp.text)
            if ids:
                self.client.get(
                    f"/student/results/{ids[0]}",
                    name="GET /student/results/[id]",
                )

    @task(2)
    def health_check(self):
        """GET /health — verify server is alive."""
        self.client.get("/health", name="GET /health")

    def on_stop(self):
        """Logout / cleanup."""
        pass
