-- Current IceWarp webclient schema (v14.3.0.8).
-- Applied to disposable test_wc_* databases only.

CREATE TABLE `folder` (
  `folder_id` int NOT NULL AUTO_INCREMENT,
  `parent_folder_id` int DEFAULT NULL,
  `account_id` text COLLATE utf8mb4_czech_ci NOT NULL,
  `name` text COLLATE utf8mb4_czech_ci NOT NULL,
  `rights` int NOT NULL DEFAULT '0',
  `attributes` int NOT NULL DEFAULT '0',
  `sync` char(1) COLLATE utf8mb4_czech_ci DEFAULT NULL,
  `path` text COLLATE utf8mb4_czech_ci,
  `uid_validity` text COLLATE utf8mb4_czech_ci,
  `sync_update` int NOT NULL DEFAULT '0',
  `unseen` int NOT NULL DEFAULT '0',
  `messages` int NOT NULL DEFAULT '0',
  `subscription_type` text COLLATE utf8mb4_czech_ci,
  `sync_in_progress_folder_id` int DEFAULT NULL,
  PRIMARY KEY (`folder_id`),
  UNIQUE KEY `FdrName` (`account_id`(191),`name`(191)),
  KEY `IDX_folder_account` (`account_id`(32),`folder_id`),
  KEY `IDX_folder_parent` (`parent_folder_id`),
  KEY `IDX_folder_name` (`account_id`(32),`name`(32))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci;

CREATE TABLE `item` (
  `item_id` int NOT NULL AUTO_INCREMENT,
  `folder_id` int NOT NULL,
  `rid` text COLLATE utf8mb4_czech_ci NOT NULL,
  `message_id` varchar(512) COLLATE utf8mb4_czech_ci DEFAULT NULL,
  `size` int NOT NULL,
  `date` int NOT NULL,
  `header_from` text COLLATE utf8mb4_czech_ci,
  `header_to` text COLLATE utf8mb4_czech_ci,
  `header_cc` text COLLATE utf8mb4_czech_ci,
  `header_bcc` text COLLATE utf8mb4_czech_ci,
  `header_sms` text COLLATE utf8mb4_czech_ci,
  `subject` text COLLATE utf8mb4_czech_ci,
  `priority` int NOT NULL DEFAULT '0',
  `flags` int NOT NULL DEFAULT '0',
  `unread` int NOT NULL DEFAULT '0',
  `body` text COLLATE utf8mb4_czech_ci,
  `static_flags` int NOT NULL DEFAULT '0',
  `smime_status` int NOT NULL DEFAULT '0',
  `has_attachment` varchar(1) COLLATE utf8mb4_czech_ci DEFAULT 'F',
  `color` varchar(1) COLLATE utf8mb4_czech_ci DEFAULT 'Z',
  `completed_on` varchar(32) COLLATE utf8mb4_czech_ci DEFAULT NULL,
  `sort_subject` text COLLATE utf8mb4_czech_ci,
  `sort_from` text COLLATE utf8mb4_czech_ci,
  `sort_to` text COLLATE utf8mb4_czech_ci,
  `sort_cc` text COLLATE utf8mb4_czech_ci,
  `sort_bcc` text COLLATE utf8mb4_czech_ci,
  `sort_sms` text COLLATE utf8mb4_czech_ci,
  `msg_file` text COLLATE utf8mb4_czech_ci,
  `flag_update` int NOT NULL DEFAULT '0',
  `source_folder_id` int DEFAULT NULL,
  `dummy_id` int DEFAULT NULL,
  `is_hidden` int NOT NULL DEFAULT '0',
  `taglist` text COLLATE utf8mb4_czech_ci,
  `item_moved` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`item_id`),
  KEY `IDX_item_date` (`folder_id`,`date`),
  KEY `IDX_item_list` (`folder_id`,`unread`,`is_hidden`),
  KEY `IDX_item_flag_update` (`folder_id`,`flag_update`),
  KEY `IDX_item_rid` (`folder_id`,`rid`(16)),
  KEY `IDX_item_source_folder_id` (`source_folder_id`,`flag_update`),
  KEY `IDX_item_source_folder_id_dummy` (`source_folder_id`,`dummy_id`),
  KEY `IDX_item_sort_from` (`folder_id`,`sort_from`(16)),
  KEY `IDX_item_sort_to` (`folder_id`,`sort_to`(16))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci;

CREATE TABLE `snoozed_item` (
  `snoozed_item_id` int NOT NULL,
  `snoozed_account_id` text COLLATE utf8mb4_czech_ci,
  `original_date` int DEFAULT NULL,
  PRIMARY KEY (`snoozed_item_id`),
  KEY `IDX_snoozed_date` (`snoozed_account_id`(128),`original_date` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci;

CREATE TABLE `metadata` (
  `item_key` varchar(255) NOT NULL,
  `item_value` text,
  PRIMARY KEY (`item_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci;
