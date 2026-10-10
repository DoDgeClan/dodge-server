BEGIN; SET LOCAL ROLE dodge_writer; SET LOCAL search_path TO dodge_private,pg_catalog;
DO $test$ DECLARE mid bigint; BEGIN
INSERT INTO guests VALUES('migration_check_a','migration_check_hash_a','CheckA','migration_check_a',0);
INSERT INTO guests VALUES('migration_check_b','migration_check_hash_b','CheckB','migration_check_b',0);
INSERT INTO friends VALUES('migration_check_a','migration_check_b',1);
INSERT INTO chat_pairs(pair,a,b) VALUES('migration_check_a:migration_check_b','migration_check_a','migration_check_b');
INSERT INTO chat_preferences VALUES('migration_check_a','{"language":"ru","requests":false}') ON CONFLICT(uid) DO UPDATE SET data=excluded.data;
INSERT INTO chat_messages(pair,sender,nonce,kind,text,style,created) VALUES('migration_check_a:migration_check_b','migration_check_a','check_nonce_0001','text','Привет 😊',49,123.5) RETURNING id INTO mid;
INSERT INTO chat_messages(pair,sender,nonce,kind,text,style,created) VALUES('migration_check_a:migration_check_b','migration_check_a','check_nonce_0001','text','Привет 😊',49,123.5) ON CONFLICT DO NOTHING;
IF (SELECT count(*) FROM chat_messages WHERE sender='migration_check_a')<>1 THEN RAISE EXCEPTION 'duplicate_message'; END IF;
UPDATE chat_messages SET delivered=COALESCE(delivered,124.5) WHERE id=mid;
IF (SELECT text FROM chat_messages WHERE id=mid)<>'Привет 😊' THEN RAISE EXCEPTION 'unicode_corrupt'; END IF;
INSERT INTO chat_media VALUES('migration_check_media','migration_check_a','photo','image/jpeg',decode('ffd8ffd9','hex'),123.5);
IF (SELECT length(data) FROM chat_media WHERE id='migration_check_media')<>4 THEN RAISE EXCEPTION 'binary_corrupt'; END IF;
INSERT INTO chat_events VALUES('migration_check_warning','migration_check_a','streak_notifications','{}',123.5,0) ON CONFLICT DO NOTHING;
INSERT INTO chat_events VALUES('migration_check_warning','migration_check_a','streak_notifications','{}',123.5,0) ON CONFLICT DO NOTHING;
IF (SELECT count(*) FROM chat_events WHERE id='migration_check_warning')<>1 THEN RAISE EXCEPTION 'duplicate_event'; END IF;
END $test$; ROLLBACK;
