"""Locust load test — ScanGrade 1000 concurrent students.

Simulates realistic student behavior during an exam session:
1. Login as demo student
2. Browse available exams
3. Open an exam (load questions)
4. Submit exam with sample answers
5. View results

Usage:
  locust -f locustfile.py --host=https://scangrade.web.id --users=1000 --spawn-rate=50 --run-time=10m --headless --csv=loadtest1000
"""
import random
import re
from locust import HttpUser, task, between


DEMO_EMAILS = [f"siswa{i}_smp@scan-grade.app" for i in range(1, 1001)]


class ScanGradeStudent(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.email = random.choice(DEMO_EMAILS)
        self.password = "demo123"
        self.logged_in = False
        self.exam_ids = []

        resp = self.client.post(
            "/auth/login-user",
            data={"email": self.email, "password": self.password},
            name="POST login-user",
        )
        if resp.status_code in (200, 302):
            self.logged_in = True

    @task(5)
    def view_dashboard(self):
        if not self.logged_in:
            return
        self.client.get("/student/dashboard", name="GET dashboard")

    @task(8)
    def view_exams(self):
        if not self.logged_in:
            return
        resp = self.client.get("/student/exams", name="GET exams list")
        if resp.status_code == 200:
            ids = re.findall(r'/student/exams/([a-f0-9\-]{36})', resp.text)
            if ids:
                self.exam_ids = list(set(ids))

    @task(4)
    def open_exam(self):
        if not self.logged_in or not self.exam_ids:
            return
        exam_id = random.choice(self.exam_ids)
        self.client.get(f"/student/exams/{exam_id}", name="GET exam detail")

    @task(1)
    def view_results(self):
        if not self.logged_in:
            return
        self.client.get("/student/results", name="GET results list")

    @task(1)
    def view_result_detail(self):
        if not self.logged_in:
            return
        resp = self.client.get("/student/results", name="GET results list (detail)")
        if resp.status_code == 200:
            ids = re.findall(r'/student/results/([a-f0-9\-]{36})', resp.text)
            if ids:
                self.client.get(f"/student/results/{ids[0]}", name="GET result detail")

    @task(3)
    def health_check(self):
        self.client.get("/health", name="GET health")
