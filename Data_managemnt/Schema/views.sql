CREATE VIEW `members_points` AS
SELECT 
    `m`.`id` AS `id`,
    `m`.`name` AS `member name`,
    `a`.`name` AS `action name`,
    `a`.`points` AS `points`
FROM 
    `members` AS `m`
JOIN `members_logs` AS `ml` ON `ml`.`member_id` = `m`.`id` 
JOIN `logs` AS `l` ON `l`.`id` = `ml`.`log_id`
JOIN `actions` AS `a` ON `a`.`id` = `l`.`action_id`;


CREATE VIEW `departments_points` AS 
SELECT 
    `d`.`id` AS `id`,
    `d`.`name` AS `department name`,
    `a`.`name` AS `action name`,
    `a`.`points` AS `points`
FROM 
    `departments` AS `d`
JOIN `departments_logs` AS `dl` ON `dl`.`department_id` = `d`.`id`
JOIN `logs` AS `l` ON `l`.`id` = `dl`.`log_id`
JOIN `actions` AS `a` ON `a`.`id` = `l`.`action_id`; 