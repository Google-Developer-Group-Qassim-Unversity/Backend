CREATE TABLE `departments`(
    `id` INT UNSIGNED AUTO_INCREMENT,
    `name` VARCHAR(50) NOT NULL,
    PRIMARY KEY (`id`)
);


CREATE TABLE `members`(
    `id` INT UNSIGNED AUTO_INCREMENT,
    `name` VARCHAR(50) NOT NULL,
    `email` VARCHAR(100) UNIQUE, 
    `phone_number` VARCHAR(20) UNIQUE, 
    `uni_id` VARCHAR(50) NOT NULL UNIQUE,
    PRIMARY KEY (`id`)
);


CREATE TABLE `actions`(
    `id` INT UNSIGNED AUTO_INCREMENT,
    `name` VARCHAR(60) NOT NULL UNIQUE, 
    `points` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`id`) 
);


CREATE TABLE `logs` (
    `id` INT UNSIGNED AUTO_INCREMENT,
    `action_id` INT UNSIGNED, 
    `start_date` DATE NOT NULL, 
    `end_date` DATE NOT NULL,
    PRIMARY KEY (`id`),
    FOREIGN KEY (`action_id`) REFERENCES `actions`(`id`) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE `departments_logs`(
    `id` INT UNSIGNED AUTO_INCREMENT,
    `department_id` INT UNSIGNED,
    `log_id` INT UNSIGNED, 
    `mf` INT NOT NULL DEFAULT 1, 
    `attendants_number` INT UNSIGNED DEFAULT NULL, 
    PRIMARY KEY (`id`), 
    FOREIGN KEY (`department_id`) REFERENCES `departments`(`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (`log_id`) REFERENCES `logs`(`id`) ON DELETE CASCADE ON UPDATE CASCADE
    );


CREATE TABLE `members_logs`(
    `id` INT UNSIGNED AUTO_INCREMENT,
    `member_id` INT UNSIGNED,
    `log_id` INT UNSIGNED, 
    PRIMARY KEY (`id`), 
    FOREIGN KEY (`member_id`) REFERENCES `members`(`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (`log_id`) REFERENCES `logs`(`id`) ON DELETE CASCADE ON UPDATE CASCADE
);


CREATE TABLE `modifications`(
    `id` INT UNSIGNED AUTO_INCREMENT, 
    `log_id` INT UNSIGNED,
    `type` ENUM('bonus', 'discount') NOT NULL,
    `value` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`id`),
    FOREIGN KEY (`log_id`) REFERENCES `logs`(`id`) ON DELETE CASCADE ON UPDATE CASCADE
);
