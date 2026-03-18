/*M!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19-12.2.2-MariaDB, for Linux (x86_64)
--
-- Host: 84.8.121.6    Database: scores
-- ------------------------------------------------------
-- Server version	9.6.1-cloud

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*M!100616 SET @OLD_NOTE_VERBOSITY=@@NOTE_VERBOSITY, NOTE_VERBOSITY=0 */;

--
-- Table structure for table `actions`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `actions` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `action_name` varchar(60) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `points` int unsigned NOT NULL,
  `action_type` enum('composite','department','member','bonus') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `ar_action_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `order` int NOT NULL DEFAULT '99',
  `is_hidden` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=116 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `departments`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `departments` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  `type` enum('administrative','practical') NOT NULL,
  `ar_name` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `departments_logs`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `departments_logs` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `department_id` int unsigned NOT NULL,
  `log_id` int unsigned NOT NULL,
  PRIMARY KEY (`id`),
  KEY `departments_logs_departments_FK` (`department_id`),
  KEY `departments_logs_idx` (`log_id`,`department_id`) USING BTREE,
  CONSTRAINT `departments_logs_departments_FK` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `departments_logs_logs_FK` FOREIGN KEY (`log_id`) REFERENCES `logs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=231 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary table structure for view `departments_points`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `departments_points` AS SELECT
 1 AS `department_id`,
  1 AS `department_name`,
  1 AS `department_type`,
  1 AS `ar_department_name`,
  1 AS `total_points` */;
SET character_set_client = @saved_cs_client;

--
-- Temporary table structure for view `departments_points_history`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `departments_points_history` AS SELECT
 1 AS `department_id`,
  1 AS `department_name`,
  1 AS `ar_department_name`,
  1 AS `event_id`,
  1 AS `event_name`,
  1 AS `start_datetime`,
  1 AS `end_datetime`,
  1 AS `status`,
  1 AS `action_name`,
  1 AS `ar_action_name`,
  1 AS `location_type`,
  1 AS `points` */;
SET character_set_client = @saved_cs_client;

--
-- Table structure for table `events`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `events` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci,
  `location_type` enum('online','on-site','none','hidden') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `location` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `start_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `end_datetime` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `status` enum('draft','open','active','closed') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `image_url` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `is_official` tinyint(1) DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `event_name` (`name`),
  KEY `events_id_IDX` (`id`,`name`) USING BTREE
) ENGINE=InnoDB AUTO_INCREMENT=228 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `forms`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `forms` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `event_id` int unsigned NOT NULL,
  `google_form_id` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `google_refresh_token` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `form_type` enum('none','registration','google') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `google_watch_id` varchar(100) DEFAULT NULL,
  `google_responders_url` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `forms_unique_event_id` (`event_id`),
  CONSTRAINT `forms_ibfk_1` FOREIGN KEY (`event_id`) REFERENCES `events` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=91 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary table structure for view `forms_submissions`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `forms_submissions` AS SELECT
 1 AS `submission_id`,
  1 AS `submitted_at`,
  1 AS `form_type`,
  1 AS `submission_type`,
  1 AS `id`,
  1 AS `name`,
  1 AS `email`,
  1 AS `phone_number`,
  1 AS `uni_id`,
  1 AS `gender`,
  1 AS `uni_level`,
  1 AS `uni_college`,
  1 AS `is_accepted`,
  1 AS `google_submission_value`,
  1 AS `event_id`,
  1 AS `form_id`,
  1 AS `google_form_id` */;
SET character_set_client = @saved_cs_client;

--
-- Table structure for table `logs`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `logs` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `action_id` int unsigned NOT NULL,
  `event_id` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `action_id` (`action_id`),
  KEY `fk_events` (`event_id`),
  CONSTRAINT `fk_events` FOREIGN KEY (`event_id`) REFERENCES `events` (`id`) ON DELETE CASCADE,
  CONSTRAINT `logs_ibfk_1` FOREIGN KEY (`action_id`) REFERENCES `actions` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=387 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary table structure for view `member_event_history`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `member_event_history` AS SELECT
 1 AS `member_id`,
  1 AS `member_name`,
  1 AS `event_id`,
  1 AS `event_name`,
  1 AS `start_datetime`,
  1 AS `end_datetime`,
  1 AS `location_type`,
  1 AS `points`,
  1 AS `action_name`,
  1 AS `ar_action_name` */;
SET character_set_client = @saved_cs_client;

--
-- Table structure for table `members`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `members` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  `email` varchar(100) DEFAULT NULL,
  `phone_number` varchar(20) DEFAULT NULL,
  `uni_id` varchar(50) NOT NULL,
  `gender` enum('Male','Female') NOT NULL,
  `uni_level` int NOT NULL,
  `uni_college` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_authenticated` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uni_id` (`uni_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1601 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `members_logs`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `members_logs` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `member_id` int unsigned NOT NULL,
  `log_id` int unsigned NOT NULL,
  `date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_member_log_day` (`member_id`,`log_id`,`date`),
  KEY `fk_members_id` (`member_id`) USING BTREE,
  KEY `idx_members_logs_log_id` (`log_id`) USING BTREE,
  CONSTRAINT `fk_members_id` FOREIGN KEY (`member_id`) REFERENCES `members` (`id`),
  CONSTRAINT `members_logs_logs_FK` FOREIGN KEY (`log_id`) REFERENCES `logs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=5599 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary table structure for view `members_points`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `members_points` AS SELECT
 1 AS `member_id`,
  1 AS `member_name`,
  1 AS `total_points` */;
SET character_set_client = @saved_cs_client;

--
-- Table structure for table `modifications`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `modifications` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `log_id` int unsigned NOT NULL,
  `type` enum('bonus','discount') NOT NULL,
  `value` int unsigned NOT NULL,
  PRIMARY KEY (`id`),
  KEY `log_id` (`log_id`),
  CONSTRAINT `modifications_ibfk_1` FOREIGN KEY (`log_id`) REFERENCES `logs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=112 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary table structure for view `open_events`
--

SET @saved_cs_client     = @@character_set_client;
SET character_set_client = utf8mb4;
/*!50001 CREATE VIEW `open_events` AS SELECT
 1 AS `id`,
  1 AS `name`,
  1 AS `description`,
  1 AS `location_type`,
  1 AS `location`,
  1 AS `start_datetime`,
  1 AS `end_datetime`,
  1 AS `status`,
  1 AS `image_url`,
  1 AS `is_official`,
  1 AS `form_id`,
  1 AS `form_type`,
  1 AS `google_responders_url` */;
SET character_set_client = @saved_cs_client;

--
-- Table structure for table `role`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `role` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `member_id` int unsigned NOT NULL,
  `role` enum('admin','super_admin','admin_points','none') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_role_member` (`member_id`),
  CONSTRAINT `fk_role_member` FOREIGN KEY (`member_id`) REFERENCES `members` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=39 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `submissions`
--

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `submissions` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `form_id` int unsigned NOT NULL,
  `member_id` int unsigned NOT NULL,
  `is_accepted` tinyint(1) NOT NULL DEFAULT '0',
  `google_submission_id` varchar(100) DEFAULT NULL,
  `google_submission_value` json DEFAULT NULL,
  `submitted_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `submission_type` enum('none','registration','partial','google') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `submissions_unique` (`member_id`,`form_id`),
  KEY `from_id_member_id_idx` (`form_id`,`member_id`),
  CONSTRAINT `submissions_ibfk_1` FOREIGN KEY (`form_id`) REFERENCES `forms` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `submissions_ibfk_2` FOREIGN KEY (`member_id`) REFERENCES `members` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=347 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping routines for database 'scores'
--

--
-- Final view structure for view `departments_points`
--

/*!50001 DROP VIEW IF EXISTS `departments_points`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `departments_points` AS select `d`.`id` AS `department_id`,`d`.`name` AS `department_name`,`d`.`type` AS `department_type`,`d`.`ar_name` AS `ar_department_name`,coalesce(sum((case when (`e`.`id` is not null) then ((coalesce(`a`.`points`,0) + coalesce(`m`.`bonus`,0)) - coalesce(`m`.`discount`,0)) else 0 end)),0) AS `total_points` from (((((`departments` `d` left join `departments_logs` `dl` on((`dl`.`department_id` = `d`.`id`))) left join `logs` `l` on((`l`.`id` = `dl`.`log_id`))) left join `events` `e` on(((`e`.`id` = `l`.`event_id`) and (`e`.`end_datetime` > '2026-01-18') and (`e`.`end_datetime` < '2026-08-23') and (`e`.`status` <> 'draft')))) left join `actions` `a` on((`a`.`id` = `l`.`action_id`))) left join (select `modifications`.`log_id` AS `log_id`,sum((case when (`modifications`.`type` = 'bonus') then coalesce(`modifications`.`value`,0) else 0 end)) AS `bonus`,sum((case when (`modifications`.`type` = 'discount') then coalesce(`modifications`.`value`,0) else 0 end)) AS `discount` from `modifications` group by `modifications`.`log_id`) `m` on((`m`.`log_id` = `l`.`id`))) group by `d`.`id`,`d`.`name`,`d`.`type`,`d`.`ar_name` order by `total_points` desc */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `departments_points_history`
--

/*!50001 DROP VIEW IF EXISTS `departments_points_history`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `departments_points_history` AS select `dl`.`department_id` AS `department_id`,`d`.`name` AS `department_name`,`d`.`ar_name` AS `ar_department_name`,`l`.`event_id` AS `event_id`,`e`.`name` AS `event_name`,`e`.`start_datetime` AS `start_datetime`,`e`.`end_datetime` AS `end_datetime`,`e`.`status` AS `status`,`a`.`action_name` AS `action_name`,`a`.`ar_action_name` AS `ar_action_name`,`e`.`location_type` AS `location_type`,sum((coalesce(`a`.`points`,0) + coalesce(`mods`.`mod_value_sum`,0))) AS `points` from (((((`departments_logs` `dl` join `departments` `d` on((`d`.`id` = `dl`.`department_id`))) join `logs` `l` on((`l`.`id` = `dl`.`log_id`))) join `events` `e` on((`e`.`id` = `l`.`event_id`))) join `actions` `a` on((`a`.`id` = `l`.`action_id`))) left join (select `mo`.`log_id` AS `log_id`,sum((case when (`mo`.`type` = 'discount') then -(abs(coalesce(`mo`.`value`,0))) when (`mo`.`type` = 'bonus') then abs(coalesce(`mo`.`value`,0)) else coalesce(`mo`.`value`,0) end)) AS `mod_value_sum` from `modifications` `mo` group by `mo`.`log_id`) `mods` on((`mods`.`log_id` = `l`.`id`))) where ((`e`.`end_datetime` > '2026-01-18') and (`e`.`end_datetime` < '2026-08-23') and (`e`.`status` <> 'draft')) group by `dl`.`department_id`,`d`.`name`,`d`.`ar_name`,`l`.`event_id`,`e`.`name`,`e`.`start_datetime`,`e`.`end_datetime`,`l`.`action_id`,`a`.`action_name`,`a`.`ar_action_name` order by `e`.`start_datetime` desc */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `forms_submissions`
--

/*!50001 DROP VIEW IF EXISTS `forms_submissions`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `forms_submissions` AS select `s`.`id` AS `submission_id`,`s`.`submitted_at` AS `submitted_at`,`f`.`form_type` AS `form_type`,`s`.`submission_type` AS `submission_type`,`m`.`id` AS `id`,`m`.`name` AS `name`,`m`.`email` AS `email`,`m`.`phone_number` AS `phone_number`,`m`.`uni_id` AS `uni_id`,`m`.`gender` AS `gender`,`m`.`uni_level` AS `uni_level`,`m`.`uni_college` AS `uni_college`,`s`.`is_accepted` AS `is_accepted`,`s`.`google_submission_value` AS `google_submission_value`,`f`.`event_id` AS `event_id`,`f`.`id` AS `form_id`,`f`.`google_form_id` AS `google_form_id` from ((`submissions` `s` join `forms` `f` on((`s`.`form_id` = `f`.`id`))) join `members` `m` on((`s`.`member_id` = `m`.`id`))) */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `member_event_history`
--

/*!50001 DROP VIEW IF EXISTS `member_event_history`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `member_event_history` AS with `log_modifications` as (select `modifications`.`log_id` AS `log_id`,sum((case when (`modifications`.`type` = 'discount') then -(abs(coalesce(`modifications`.`value`,0))) when (`modifications`.`type` = 'bonus') then abs(coalesce(`modifications`.`value`,0)) else coalesce(`modifications`.`value`,0) end)) AS `mod_value_sum` from `modifications` group by `modifications`.`log_id`) select `m`.`id` AS `member_id`,`m`.`name` AS `member_name`,`e`.`id` AS `event_id`,`e`.`name` AS `event_name`,`e`.`start_datetime` AS `start_datetime`,`e`.`end_datetime` AS `end_datetime`,`e`.`location_type` AS `location_type`,sum((coalesce(`a`.`points`,0) + coalesce(`lm`.`mod_value_sum`,0))) AS `points`,group_concat(distinct `a`.`action_name` order by `a`.`action_name` ASC separator ', ') AS `action_name`,group_concat(distinct `a`.`ar_action_name` order by `a`.`action_name` ASC separator ', ') AS `ar_action_name` from (((((`members` `m` join `members_logs` `ml` on((`m`.`id` = `ml`.`member_id`))) join `logs` `l` on((`ml`.`log_id` = `l`.`id`))) join `events` `e` on((`l`.`event_id` = `e`.`id`))) join `actions` `a` on((`l`.`action_id` = `a`.`id`))) left join `log_modifications` `lm` on((`l`.`id` = `lm`.`log_id`))) where ((`e`.`end_datetime` > '2026-01-18') and (`e`.`end_datetime` < '2026-08-23') and (`e`.`status` <> 'draft')) group by `m`.`id`,`m`.`name`,`e`.`id`,`e`.`name`,`e`.`start_datetime`,`e`.`end_datetime` order by `e`.`start_datetime` desc */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `members_points`
--

/*!50001 DROP VIEW IF EXISTS `members_points`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `members_points` AS select `m`.`id` AS `member_id`,`m`.`name` AS `member_name`,coalesce(sum((case when (`e`.`id` is not null) then (coalesce(`a`.`points`,0) + coalesce(`mods`.`mod_value_sum`,0)) else 0 end)),0) AS `total_points` from (((((`members` `m` left join `members_logs` `ml` on((`ml`.`member_id` = `m`.`id`))) left join `logs` `l` on((`l`.`id` = `ml`.`log_id`))) left join `events` `e` on(((`e`.`id` = `l`.`event_id`) and (`e`.`end_datetime` > '2026-01-18') and (`e`.`end_datetime` < '2026-08-23') and (`e`.`status` <> 'draft')))) left join `actions` `a` on((`a`.`id` = `l`.`action_id`))) left join (select `mo`.`log_id` AS `log_id`,sum((case when (`mo`.`type` = 'discount') then -(abs(coalesce(`mo`.`value`,0))) when (`mo`.`type` = 'bonus') then abs(coalesce(`mo`.`value`,0)) else coalesce(`mo`.`value`,0) end)) AS `mod_value_sum` from `modifications` `mo` group by `mo`.`log_id`) `mods` on((`mods`.`log_id` = `l`.`id`))) group by `m`.`id`,`m`.`name` order by `total_points` desc */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;

--
-- Final view structure for view `open_events`
--

/*!50001 DROP VIEW IF EXISTS `open_events`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = utf8mb4 */;
/*!50001 SET character_set_results     = utf8mb4 */;
/*!50001 SET collation_connection      = utf8mb4_0900_ai_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`scores`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `open_events` AS select `e`.`id` AS `id`,`e`.`name` AS `name`,`e`.`description` AS `description`,`e`.`location_type` AS `location_type`,`e`.`location` AS `location`,`e`.`start_datetime` AS `start_datetime`,`e`.`end_datetime` AS `end_datetime`,`e`.`status` AS `status`,`e`.`image_url` AS `image_url`,`e`.`is_official` AS `is_official`,`f`.`id` AS `form_id`,`f`.`form_type` AS `form_type`,`f`.`google_responders_url` AS `google_responders_url` from (`events` `e` join `forms` `f` on((`f`.`event_id` = `e`.`id`))) where (`e`.`status` = 'open') */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*M!100616 SET NOTE_VERBOSITY=@OLD_NOTE_VERBOSITY */;

-- Dump completed on 2026-03-18 21:59:09
