-- ============================================================
--  MSSCO IT Helpdesk — Database Setup
--  Run once:  mysql -u root -p < schema.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS it_ticket_system
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE it_ticket_system;

CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(80)  NOT NULL UNIQUE,
    full_name   VARCHAR(120),
    email       VARCHAR(120) UNIQUE,
    department  VARCHAR(80),
    password    VARCHAR(255) NOT NULL,
    role        ENUM('employee','admin') NOT NULL DEFAULT 'employee',
    is_active           TINYINT(1) NOT NULL DEFAULT 1,
    pending_approval    TINYINT(1) NOT NULL DEFAULT 0,
    specialization      VARCHAR(50),
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    description TEXT,
    category    VARCHAR(50)  NOT NULL,
    priority    ENUM('Low','Medium','High','Urgent') NOT NULL DEFAULT 'Medium',
    status      ENUM('Open','In Progress','Closed') NOT NULL DEFAULT 'Open',
    created_by  INT NOT NULL,
    assigned_to INT,
    resolved_at DATETIME,
    sla_due_at  DATETIME,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by)  REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id  INT  NOT NULL,
    user_id    INT  NOT NULL,
    comment    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_history (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    ticket_id  INT NOT NULL,
    changed_by INT NOT NULL,
    old_status VARCHAR(20),
    new_status VARCHAR(20),
    note       TEXT,
    changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_id)  REFERENCES tickets(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id)   ON DELETE CASCADE
);

-- Default admin account
-- Username : admin
-- Password : Admin@1234   (change this after first login)
INSERT IGNORE INTO users (username, full_name, email, department, password, role)
VALUES (
    'admin',
    'IT Administrator',
    'admin@mssco.com',
    'IT',
    'scrypt:32768:8:1$DpyTVFnWQXuh6muV$a51be4b9e7b4266c24a922069575fbc2479095f5d4aaf0146b75e8305d4544306788dcf9e88371dab35a9f3ef7798f2cfd16747b8455aa9e82908e0ef17e862a',
    'admin'
);
