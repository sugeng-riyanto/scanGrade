-- Migration 020: Backfill conversation_id for existing notifications
-- Menautkan notifikasi pertama ke percakapan yang sudah dibuat

UPDATE notifications n
SET conversation_id = c.id
FROM conversations c
WHERE n.conversation_id IS NULL
  AND (
    (c.participant_1 = n.sender_id AND c.created_at <= n.created_at + interval '1 minute')
    OR
    (c.participant_2 = n.sender_id AND c.created_at <= n.created_at + interval '1 minute')
  );
