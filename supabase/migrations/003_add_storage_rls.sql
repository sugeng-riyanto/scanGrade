-- Add RLS policies for exam-pdfs storage bucket
CREATE POLICY "exam_pdfs_select" ON storage.objects
  FOR SELECT TO public USING (bucket_id = 'exam-pdfs');

CREATE POLICY "exam_pdfs_insert" ON storage.objects
  FOR INSERT TO authenticated WITH CHECK (bucket_id = 'exam-pdfs');

CREATE POLICY "exam_pdfs_delete" ON storage.objects
  FOR DELETE TO authenticated USING (bucket_id = 'exam-pdfs');
