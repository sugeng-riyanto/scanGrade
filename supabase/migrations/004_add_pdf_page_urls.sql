-- Migration 004: Add pdf_page_urls to exams for PNG-rendered pages
ALTER TABLE exams ADD COLUMN IF NOT EXISTS pdf_page_urls JSONB DEFAULT '[]';
