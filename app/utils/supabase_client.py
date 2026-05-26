from flask import current_app
from supabase import Client


def get_supabase() -> Client:
    return current_app.extensions["supabase"]
