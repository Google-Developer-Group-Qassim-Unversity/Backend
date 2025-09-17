INSERT INTO `departments_logs` (`log_id`, `department_id`, `attendants_number`)
VALUES
(
    9,
    (SELECT `id` FROM `departments` 
    WHERE `name` = 'Tech and Business'), 
    (SELECT COUNT(*) FROM `members_logs` 
    WHERE `log_id` = 10)

);
