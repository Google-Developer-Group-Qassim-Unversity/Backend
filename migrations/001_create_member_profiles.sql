-- Migration: 001_create_member_profiles.sql
-- Description: Create member_profiles table linked to members.id (1-to-1 relationship)

-- =============================================================================
-- UP MIGRATION
-- =============================================================================

CREATE TABLE IF NOT EXISTS `member_profiles` (
  `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `member_id` INT UNSIGNED NOT NULL UNIQUE,
  `uuid` VARCHAR(64) NOT NULL UNIQUE,
  `theme_id` VARCHAR(50) NOT NULL DEFAULT 'gdg-blue',
  `name_language` ENUM('ar', 'en') NOT NULL DEFAULT 'ar',
  `bio` TEXT NULL,
  `social_links` JSON NULL,
  `visibility` JSON NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  INDEX `idx_member_profiles_uuid` (`uuid`),
  CONSTRAINT `fk_member_profiles_member`
    FOREIGN KEY (`member_id`) REFERENCES `members` (`id`)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- =============================================================================
-- DOWN MIGRATION (Rollback)
-- =============================================================================
-- DROP TABLE IF EXISTS `member_profiles`;
