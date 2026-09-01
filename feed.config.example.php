<?php
/**
 * feed.config.example.php — образец конфига подключения для feed.php.
 *
 * СКОПИРУЙТЕ этот файл в feed.config.php и впишите реальные параметры БД
 * (те же, что использует import-pdo.php / config.core.php вашего сайта).
 *
 * feed.config.php НЕ коммитится в репозиторий (см. .gitignore) — креды
 * не попадают в код и в git.
 *
 * Вариант А (рекомендуется): переиспользовать существующий конфиг сайта.
 *   Если config.core.php уже определяет переменные подключения — подключите
 *   его здесь и верните их, например:
 *
 *     require __DIR__ . '/config.core.php';
 *     return ['db' => [
 *         'host' => $db_host, 'name' => $db_name,
 *         'user' => $db_user, 'pass' => $db_pass, 'port' => 3306,
 *     ]];
 *
 * Вариант Б (ниже): задать параметры прямо здесь.
 */

return [
    'db' => [
        'host' => 'localhost',
        'port' => 3306,
        'name' => 'ИМЯ_БАЗЫ',     // как в import-pdo.php
        'user' => 'ПОЛЬЗОВАТЕЛЬ',
        'pass' => 'ПАРОЛЬ',
    ],
];
