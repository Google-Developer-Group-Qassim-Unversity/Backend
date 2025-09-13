START TRANSACTION;

-- Add Members logs
INSERT INTO logs (action_id, start_date, end_date, event_name)
VALUES (
    (SELECT `id` FROM `actions` WHERE `actions`.`name` = 'On-site course attendance'),
    '2025-09-11',
    '2025-09-11',
    'Git & Github مقدمة لـ'
);

SET @member_log_id = LAST_INSERT_ID();

INSERT INTO members (name, email, phone_number, uni_id)
VALUES
('طارق فهد إبراهيم', 'tariq.s@example.com', '+966 250 123 4567', 9446897735),
('فهد سعيد سلمان الغامدي', 'fahad.s@example.com', '+966 255 987 6543', 9458334353),
('محمد محمد عبدالله السبيعي', 'mohammed.m@example.com', '+966 253 456 7890', 9451096611),
('حمد محمد صالح الغامدي', 'hamad.g@example.com', '+966 256 321 9876', 9454286931),
('خالد محمد يوسف الأنصاري', 'khalid.a@example.com', '+966 254 789 1234', 9449797690),
('سعيد علي عبدالعزيز المطيري', 'saeed.m@example.com', '+966 250 654 3210', 9440757878),
('ماجد سعيد ناصر السبيعي', 'majid.s@example.com', '+966 255 321 4567', 9467145074),
('فهد سعيد سلمان المطيري', 'fahad.m@example.com', '+966 253 987 1234', 9453387821),
('يوسف ناصر ناصر الشريف', 'yousef.s@example.com', '+966 256 123 7890', 9453518462),
('خالد إبراهيم أحمد السبيعي', 'khalid.s@example.com', '+966 254 456 9871', 9453214540);


SET @members_count = ROW_COUNT(); -- number of rows inserted
SET @first_member_id = LAST_INSERT_ID();

-- this works and it's wierd but it's actually smart.
INSERT INTO members_logs (member_id, log_id)
SELECT id, @member_log_id
FROM members
WHERE id BETWEEN @first_member_id AND @first_member_id + @members_count - 1;


-- Add department logs
INSERT INTO `logs` (action_id, start_date, end_date, event_name) VALUES 
(
    (SELECT `id` FROM `actions` WHERE `actions`.name = 'On-site course'),
    '2025-09-11', 
    '2025-09-11', 
    'Git & Github مقدمة لـ'
);

SET @department_log_id = LAST_INSERT_ID();

INSERT INTO `departments_logs` (department_id, log_id, attendants_number) VALUES
(
    (SELECT `id` FROM `departments` WHERE `departments`.`name` = 'Tech and Business'),
    @department_log_id,
    (SELECT COUNT(*) FROM `members_logs` WHERE `members_logs`.`log_id`=@member_log_id)
);

COMMIT;
