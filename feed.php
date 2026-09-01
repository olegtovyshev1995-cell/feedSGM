<?php
/**
 * feed.php — генератор XML-фида автозагрузки Авто.ру (легковые, «С пробегом»).
 *
 * Кладётся рядом с import-pdo.php. Отдаёт актуальный список авто по постоянному
 * URL (https://ваш-домен/feed.php). Авто.ру сам забирает фид по расписанию (pull).
 *
 * Режимы:
 *   - HTTP-запрос  → генерация на лету + Content-Type: application/xml + кэш в autoru.xml
 *   - CLI (по CRON) → тихо перегенерирует статический autoru.xml (см. README)
 *
 * Безопасность: только PDO prepared, никакой конкатенации значений в SQL;
 * креды берутся из внешнего конфига (feed.config.php), не из этого файла.
 *
 * Формат XML — по официальной спеке Авто.ру для легковых б/у:
 *   <data><cars><car>…</car></cars></data>, теги строчными, UTF-8,
 *   экранирование " & > < ', vin 17 симв. без I/O/Q, price > 1500 и т.д.
 *
 * ВАЖНО: имена таблицы/колонок ниже — ПРЕДПОЛОЖЕНИЕ. Сверьте со своей схемой
 * (SHOW CREATE TABLE) и поправьте блок «НАСТРОЙКА». Логика/формат при этом
 * не меняются.
 */

declare(strict_types=1);

/* ─────────────────────────── НАСТРОЙКА ─────────────────────────── */
/* Всё, что зависит от вашей БД/схемы, собрано здесь. Правится один раз. */

/** Имя таблицы с авто (как в import-pdo.php). */
const FEED_TABLE = 'cars';

/**
 * Маппинг: поле Авто.ру => имя колонки в вашей таблице.
 * null — колонки нет (поле не будет выгружаться / возьмётся из констант ниже).
 * Значения проходят через нормализацию (см. dictionaries), не берутся «как есть»
 * для полей со строгим справочником.
 */
const FEED_COLUMNS = [
    'mark_id'        => 'mark',          // марка (текст из каталога Авто.ру)
    'folder_id'      => 'model',         // модель (текст из каталога)
    'modification_id'=> 'modification',  // модификация из каталога; если есть —
                                         // движок-теги НЕ отправляются (правило спеки)
    'body_type'      => 'body_type',     // тип кузова → строгий справочник
    'wheel'          => 'wheel',         // левый/правый
    'color'          => 'color',         // → 16 допустимых цветов
    'custom'         => 'custom',        // Растаможен/Не растаможен
    'state'          => 'state',         // Отличное/Хорошее/Среднее/Требует ремонта
    'owners_number'  => 'owners',        // число или слова → «Один владелец» …
    'run'            => 'mileage',       // пробег, км (int > 0)
    'year'           => 'year',          // год выпуска (int ≥ 1970)
    'registry_year'  => null,            // год первой регистрации; null → = year
    'doors_count'    => 'doors',         // количество дверей
    'vin'            => 'vin',           // VIN (17 симв.)
    'price'          => 'price',         // цена (int > 1500)
    'description'    => 'description',   // описание (свободный текст)
    'unique_id'      => 'id',            // fallback-идентификатор, если нет VIN

    // Альтернатива modification_id — 5 отдельных параметров двигателя.
    // Используются ТОЛЬКО если modification_id пуст (спека запрещает слать вместе).
    'engine_volume'  => 'engine_volume', // см³
    'engine_power'   => 'engine_power',  // «150 л.с.»
    'engine_type'    => 'engine_type',   // Бензин/Дизель/Гибрид/Электро/ГБО/Водородный
    'gearbox'        => 'gearbox',       // Автоматическая/Механическая/Автомат вариатор/Автомат робот
    'drive'          => 'drive',         // Задний/Передний/Полный

    // Необязательные — выгружаются «как есть», если колонка задана и непуста.
    'metallic'       => null,            // да/нет
    'sts'            => null,
    'pts'            => null,            // Оригинал/Дубликат
    'warranty_expire'=> null,            // MM.YYYY
    'exchange'       => null,            // Нет / Рассмотрю варианты
    'extras'         => null,            // опции через запятую
    'video'          => null,            // ссылка YouTube/Rutube/VK
];

/** Значения, одинаковые для всех авто (нет отдельной колонки). */
const FEED_CONST = [
    'availability' => 'В наличии',   // строго: В наличии / На заказ / В пути
    'currency'     => 'RUR',         // строго: RUR / EUR / USD
];

/** Список обязательных полей Авто.ру. Строка без любого из них — пропускается + лог.
 *  registry_year/doors_count помечены спекой как обязательные для б/у; если в вашей
 *  БД их нет и Авто.ру принимает без них — уберите из списка. */
const FEED_REQUIRED = [
    'mark_id', 'folder_id', 'body_type', 'wheel', 'color',
    'state', 'owners_number', 'run', 'year', 'price',
    'doors_count', 'registry_year',
    // vin ИЛИ unique_id проверяется отдельно (см. validate_row).
    // modification_id ИЛИ полный набор двигателя — тоже отдельно.
];

/**
 * Признак «в наличии». remove_csv.php удаляет проданные, поэтому по умолчанию
 * фильтра нет (в таблице только активные). Если у вас есть статус — задайте,
 * например: ['sql' => '`status` = :st', 'params' => [':st' => 'active']].
 */
const FEED_ACTIVE = [
    'sql'    => '',      // напр. '`status` = :st'  (пусто = все строки активны)
    'params' => [],      // напр. [':st' => 'active']
];

/**
 * Как хранятся фото:
 *   'delimited' — одна колонка со списком URL через разделитель;
 *   'table'     — отдельная таблица (одна строка = одно фото).
 */
const FEED_PHOTOS = [
    'mode'      => 'delimited',
    // mode = delimited:
    'column'    => 'photos',   // колонка со списком ссылок
    'delimiter' => '|',        // разделитель (в этом проекте исторически '|')
    // mode = table:
    'table'     => 'car_photos',
    'fk'        => 'car_id',   // ссылается на PK авто
    'url_col'   => 'url',
    'sort_col'  => 'sort',     // '' если сортировки нет
    'pk'        => 'id',       // PK авто в основной таблице (для связи с фото)
    'max'       => 40,         // Авто.ру: не более 40 фото
];

/** Путь к статическому кэшу (последний валидный фид) и к логу. */
const FEED_CACHE_FILE = __DIR__ . '/autoru.xml';
const FEED_LOG_FILE   = __DIR__ . '/feed.log';

/* ──────────────────────── КОНЕЦ НАСТРОЙКИ ──────────────────────── */


/* Словари нормализации под строгие справочники Авто.ру. Ключи — в нижнем
 * регистре без лишних пробелов; вход приводится к такому виду перед поиском. */
const DICT_COLOR = [
    'бежевый'=>'Бежевый','белый'=>'Белый','голубой'=>'Голубой','желтый'=>'Желтый',
    'жёлтый'=>'Желтый','зеленый'=>'Зеленый','зелёный'=>'Зеленый','золотой'=>'Золотой',
    'золотистый'=>'Золотой','коричневый'=>'Коричневый','красный'=>'Красный',
    'оранжевый'=>'Оранжевый','пурпурный'=>'Пурпурный','розовый'=>'Розовый',
    'серебряный'=>'Серебряный','серебристый'=>'Серебряный','серый'=>'Серый',
    'синий'=>'Синий','фиолетовый'=>'Фиолетовый','черный'=>'Черный','чёрный'=>'Черный',
];
const DICT_BODY = [
    'кабриолет'=>'Кабриолет','компактвэн'=>'Компактвэн','купе'=>'Купе',
    'купе-хардтоп'=>'Купе-хардтоп','фастбек'=>'Фастбек','фургон'=>'Фургон',
    'хэтчбек'=>'Хэтчбек','хетчбек'=>'Хэтчбек','ландо'=>'Ландо','лифтбек'=>'Лифтбек',
    'лимузин'=>'Лимузин','микровэн'=>'Микровэн','минивэн'=>'Минивэн',
    'внедорожник'=>'Внедорожник','джип'=>'Внедорожник','кроссовер'=>'Внедорожник',
    'suv'=>'Внедорожник','фаэтон-универсал'=>'Фаэтон-универсал','пикап'=>'Пикап',
    'родстер'=>'Родстер','седан'=>'Седан','тарга'=>'Тарга',
    'седан-хардтоп'=>'Седан-хардтоп','спидстер'=>'Спидстер',
    'внедорожник открытый'=>'Внедорожник открытый','универсал'=>'Универсал',
    'фаэтон'=>'Фаэтон',
];
const DICT_WHEEL   = ['левый'=>'левый','left'=>'левый','правый'=>'правый','right'=>'правый'];
const DICT_CUSTOM  = ['растаможен'=>'Растаможен','не растаможен'=>'Не растаможен',
                      'нерастаможен'=>'Не растаможен'];
const DICT_STATE   = ['отличное'=>'Отличное','хорошее'=>'Хорошее','среднее'=>'Среднее',
                      'требует ремонта'=>'Требует ремонта','битый'=>'Требует ремонта'];
const DICT_ENGINE  = ['бензин'=>'Бензин','дизель'=>'Дизель','гибрид'=>'Гибрид',
                      'электро'=>'Электро','электрический'=>'Электро','гбо'=>'ГБО',
                      'водородный'=>'Водородный'];
const DICT_GEARBOX = ['автоматическая'=>'Автоматическая','автомат'=>'Автоматическая',
                      'at'=>'Автоматическая','механическая'=>'Механическая',
                      'механика'=>'Механическая','mt'=>'Механическая',
                      'вариатор'=>'Автомат вариатор','cvt'=>'Автомат вариатор',
                      'автомат вариатор'=>'Автомат вариатор','робот'=>'Автомат робот',
                      'amt'=>'Автомат робот','автомат робот'=>'Автомат робот'];
const DICT_DRIVE   = ['задний'=>'Задний','rwd'=>'Задний','передний'=>'Передний',
                      'fwd'=>'Передний','полный'=>'Полный','awd'=>'Полный','4wd'=>'Полный',
                      '4x4'=>'Полный'];


/* ───────────────────────────── КОД ───────────────────────────── */

$IS_CLI = (PHP_SAPI === 'cli');

/** Счётчики для лога. */
$stats = ['read' => 0, 'valid' => 0, 'skipped' => 0];
$skipReasons = [];

/** Пишет строку в лог с меткой времени. Не «молчит». */
function feed_log(string $msg): void
{
    $line = '[' . date('Y-m-d H:i:s') . '] ' . $msg . PHP_EOL;
    @file_put_contents(FEED_LOG_FILE, $line, FILE_APPEND | LOCK_EX);
    error_log('feed.php: ' . $msg);
}

/**
 * Аварийный выход: НЕ отдаём пустой валидный XML (Авто.ру может снять все
 * объявления). В HTTP-режиме — 500 + последний валидный кэш, если он есть.
 */
function feed_fail(string $reason): never
{
    global $IS_CLI;
    feed_log('ОШИБКА: ' . $reason);
    if ($IS_CLI) {
        fwrite(STDERR, 'feed.php: ' . $reason . PHP_EOL);
        exit(1);
    }
    if (is_file(FEED_CACHE_FILE) && filesize(FEED_CACHE_FILE) > 0) {
        // Отдаём последний заведомо валидный фид вместо пустого/битого.
        header('Content-Type: application/xml; charset=utf-8');
        header('X-Feed-Fallback: last-valid-cache');
        readfile(FEED_CACHE_FILE);
        exit(0);
    }
    http_response_code(500);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Feed temporarily unavailable';
    exit(1);
}

/** Экранирование под XML (5 обязательных символов из спеки Авто.ру). */
function xml_escape(string $s): string
{
    // Убираем запрещённые управляющие символы (первые 32 ASCII, кроме \t\n\r).
    $s = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F]/u', '', $s) ?? $s;
    return str_replace(
        ['&', '<', '>', '"', "'"],
        ['&amp;', '&lt;', '&gt;', '&quot;', '&apos;'],
        $s
    );
}

/** Валидный идентификатор SQL (защита от инъекции через конфиг). */
function ident(string $name): string
{
    if (!preg_match('/^[A-Za-z0-9_]+$/', $name)) {
        feed_fail("Недопустимое имя колонки/таблицы в конфиге: '$name'");
    }
    return '`' . $name . '`';
}

/** Нормализация по словарю; '' если значение не распознано. */
function normalize(string $value, array $dict): string
{
    $key = mb_strtolower(trim($value));
    return $dict[$key] ?? '';
}

/** Приведение VIN к формату Авто.ру. '' если некорректен. */
function normalize_vin(string $vin): string
{
    $vin = strtoupper(trim($vin));
    return preg_match('/^[A-HJ-NPR-Z0-9]{17}$/', $vin) ? $vin : '';
}

/** «Один владелец» и т.п. из числа или текста. */
function normalize_owners(string $v): string
{
    $v = mb_strtolower(trim($v));
    $map = [
        '1'=>'Один владелец','один'=>'Один владелец','один владелец'=>'Один владелец',
        '2'=>'Два владельца','два'=>'Два владельца','два владельца'=>'Два владельца',
        '3'=>'Три владельца','три'=>'Три владельца','три владельца'=>'Три владельца',
    ];
    if (isset($map[$v])) return $map[$v];
    if (is_numeric($v) && (int)$v >= 4) return 'Четыре и более';
    if (str_contains($v, 'четыре') || str_contains($v, 'более')) return 'Четыре и более';
    return '';
}

/**
 * Собирает и валидирует одну строку БД → массив готовых полей Авто.ру.
 * Возвращает ['ok'=>bool, 'fields'=>[], 'images'=>[], 'reason'=>string].
 */
function build_row(array $r): array
{
    $col = FEED_COLUMNS;
    $raw = static fn(string $field): string =>
        (isset($col[$field]) && $col[$field] !== null && isset($r[$col[$field]]))
            ? (string)$r[$col[$field]] : '';

    $f = [];

    // Текстовые «как есть» (из каталога Авто.ру — не нормализуем).
    $f['mark_id']         = trim($raw('mark_id'));
    $f['folder_id']       = trim($raw('folder_id'));
    $f['modification_id'] = trim($raw('modification_id'));

    // Строгие справочники.
    $f['body_type'] = normalize($raw('body_type'), DICT_BODY);
    $f['wheel']     = ($raw('wheel') === '') ? 'левый' : normalize($raw('wheel'), DICT_WHEEL);
    $f['color']     = normalize($raw('color'), DICT_COLOR);
    $f['custom']    = ($raw('custom') === '') ? 'Растаможен' : normalize($raw('custom'), DICT_CUSTOM);
    $f['state']     = normalize($raw('state'), DICT_STATE);
    $f['owners_number'] = normalize_owners($raw('owners_number'));

    // Числовые.
    $run  = (int)preg_replace('/\D+/', '', $raw('run'));
    $year = (int)preg_replace('/\D+/', '', $raw('year'));
    $doors= (int)preg_replace('/\D+/', '', $raw('doors_count'));
    $price= (int)preg_replace('/\D+/', '', $raw('price'));
    $f['run']         = $run  > 0    ? (string)$run  : '';
    $f['year']        = $year >= 1970 ? (string)$year : '';
    $f['doors_count'] = $doors > 0   ? (string)$doors : '';
    $f['price']       = $price > 1500 ? (string)$price : '';

    // registry_year: из колонки или = year; должен быть ≥ year.
    $reg = (int)preg_replace('/\D+/', '', $raw('registry_year'));
    if ($reg <= 0 && $year > 0) $reg = $year;
    $f['registry_year'] = ($reg > 0 && $reg >= $year) ? (string)$reg : '';

    // VIN / unique_id.
    $f['vin']       = normalize_vin($raw('vin'));
    $f['unique_id'] = trim($raw('unique_id'));

    // Константы.
    $f['availability'] = FEED_CONST['availability'];
    $f['currency']     = FEED_CONST['currency'];

    // Описание и прочие необязательные (как есть).
    foreach (['description','metallic','sts','pts','warranty_expire','exchange','extras','video'] as $opt) {
        $val = trim($raw($opt));
        if ($val !== '') $f[$opt] = $val;
    }

    // modification_id ⊕ параметры двигателя (спека запрещает слать вместе).
    $engine = [];
    if ($f['modification_id'] === '') {
        $ev = (int)preg_replace('/\D+/', '', $raw('engine_volume'));
        if ($ev > 0)                     $engine['engine_volume'] = (string)$ev;
        if (trim($raw('engine_power')))  $engine['engine_power']  = trim($raw('engine_power'));
        $et = normalize($raw('engine_type'), DICT_ENGINE);
        if ($et)                         $engine['engine_type']   = $et;
        $gb = normalize($raw('gearbox'), DICT_GEARBOX);
        if ($gb)                         $engine['gearbox']       = $gb;
        $dr = normalize($raw('drive'), DICT_DRIVE);
        if ($dr)                         $engine['drive']         = $dr;
    }
    $f['_engine'] = $engine;

    // Фото.
    $images = collect_photos($r);

    // ── Валидация обязательных ──
    foreach (FEED_REQUIRED as $req) {
        if (!isset($f[$req]) || $f[$req] === '') {
            return ['ok' => false, 'reason' => "нет обязательного поля '$req'"];
        }
    }
    if ($f['vin'] === '' && $f['unique_id'] === '') {
        return ['ok' => false, 'reason' => 'нет ни VIN, ни unique_id'];
    }
    if ($f['modification_id'] === '' && empty($engine)) {
        return ['ok' => false, 'reason' => 'нет modification_id и параметров двигателя'];
    }

    return ['ok' => true, 'fields' => $f, 'images' => $images];
}

/** Возвращает список URL фото для строки (по режиму FEED_PHOTOS). */
function collect_photos(array $r): array
{
    $p = FEED_PHOTOS;
    $urls = [];
    if ($p['mode'] === 'delimited') {
        $col = $p['column'];
        if ($col !== '' && isset($r[$col])) {
            foreach (explode($p['delimiter'], (string)$r[$col]) as $u) {
                $u = trim($u);
                if ($u !== '') $urls[] = $u;
            }
        }
    } elseif ($p['mode'] === 'table') {
        global $photoStmt;
        $pkVal = $r[$p['pk']] ?? null;
        if ($photoStmt !== null && $pkVal !== null) {
            $photoStmt->execute([':fk' => $pkVal]);
            foreach ($photoStmt->fetchAll(PDO::FETCH_COLUMN) as $u) {
                $u = trim((string)$u);
                if ($u !== '') $urls[] = $u;
            }
        }
    }
    return array_slice(array_values(array_unique($urls)), 0, (int)$p['max']);
}

/** Один элемент <car>. */
function render_car(array $fields, array $images): string
{
    // Порядок тегов как в примере спеки для «С пробегом».
    $order = [
        'mark_id','folder_id','modification_id','body_type','wheel','color',
        'metallic','availability','custom','state','owners_number','run','year',
        'registry_year','doors_count','price','currency','vin','description',
        'extras','video','exchange','warranty_expire','pts','sts',
    ];
    $out = "  <car>\n";
    foreach ($order as $tag) {
        if (isset($fields[$tag]) && $fields[$tag] !== '') {
            $out .= "    <$tag>" . xml_escape($fields[$tag]) . "</$tag>\n";
        }
    }
    // Параметры двигателя (только когда нет modification_id).
    foreach ($fields['_engine'] ?? [] as $tag => $val) {
        $out .= "    <$tag>" . xml_escape($val) . "</$tag>\n";
    }
    // unique_id — если нет VIN.
    if (($fields['vin'] ?? '') === '' && ($fields['unique_id'] ?? '') !== '') {
        $out .= "    <unique_id>" . xml_escape($fields['unique_id']) . "</unique_id>\n";
    }
    if ($images) {
        $out .= "    <images>\n";
        foreach ($images as $u) {
            $out .= "      <image>" . xml_escape($u) . "</image>\n";
        }
        $out .= "    </images>\n";
    }
    $out .= "  </car>\n";
    return $out;
}

/**
 * Точка входа: подключение к БД → чтение → валидация → XML → вывод/кэш.
 * Вынесена в функцию, чтобы файл можно было подключить в тестах
 * (define('FEED_NO_MAIN', true)) без запуска обращения к БД.
 */
function feed_main(): void
{
global $stats, $skipReasons, $photoStmt, $IS_CLI;

/* ── Подключение к БД: креды из внешнего конфига, не из этого файла ── */
$configFile = __DIR__ . '/feed.config.php';
if (!is_file($configFile)) {
    feed_fail("Нет файла конфига $configFile (скопируйте feed.config.example.php)");
}
$CFG = require $configFile;
if (!is_array($CFG) || empty($CFG['db'])) {
    feed_fail('feed.config.php не вернул массив с ключом db');
}
$db = $CFG['db'];

try {
    $dsn = sprintf(
        'mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4',
        $db['host'] ?? 'localhost',
        (int)($db['port'] ?? 3306),
        $db['name'] ?? ''
    );
    $pdo = new PDO($dsn, $db['user'] ?? '', $db['pass'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
} catch (PDOException $e) {
    feed_fail('Не удалось подключиться к БД: ' . $e->getMessage());
}

/** Prepared-запрос фото (mode=table) — готовим один раз. */
if (FEED_PHOTOS['mode'] === 'table') {
    $p = FEED_PHOTOS;
    $sql = 'SELECT ' . ident($p['url_col']) . ' FROM ' . ident($p['table'])
         . ' WHERE ' . ident($p['fk']) . ' = :fk';
    if (($p['sort_col'] ?? '') !== '') $sql .= ' ORDER BY ' . ident($p['sort_col']) . ' ASC';
    $photoStmt = $pdo->prepare($sql);
}

/* ── Чтение авто (prepared; значения фильтра — через bound params) ── */
try {
    $sql = 'SELECT * FROM ' . ident(FEED_TABLE);
    if (FEED_ACTIVE['sql'] !== '') $sql .= ' WHERE ' . FEED_ACTIVE['sql'];
    $stmt = $pdo->prepare($sql);
    $stmt->execute(FEED_ACTIVE['params']);
    $rows = $stmt->fetchAll();
} catch (PDOException $e) {
    feed_fail('Ошибка запроса к таблице ' . FEED_TABLE . ': ' . $e->getMessage());
}

$stats['read'] = count($rows);
if ($stats['read'] === 0) {
    // Пустой результат — НЕ отдаём пустой валидный фид.
    feed_fail('Таблица вернула 0 строк — не публикуем пустой фид');
}

/* ── Сборка XML ── */
$body = '';
foreach ($rows as $i => $r) {
    $res = build_row($r);
    if (!$res['ok']) {
        $stats['skipped']++;
        $reason = $res['reason'];
        $skipReasons[$reason] = ($skipReasons[$reason] ?? 0) + 1;
        $ident = $r[FEED_COLUMNS['unique_id'] ?? 'id'] ?? ('row#' . $i);
        feed_log("ПРОПУСК [$ident]: $reason");
        continue;
    }
    $body .= render_car($res['fields'], $res['images']);
    $stats['valid']++;
}

if ($stats['valid'] === 0) {
    feed_fail('Все строки отбракованы валидацией — не публикуем пустой фид');
}

$xml = '<?xml version="1.0" encoding="utf-8"?>' . "\n"
     . "<data>\n  <cars>\n" . $body . "  </cars>\n</data>\n";

/* ── Атомарное обновление кэша (последний валидный фид) ── */
$tmp = FEED_CACHE_FILE . '.tmp';
if (@file_put_contents($tmp, $xml, LOCK_EX) !== false) {
    @rename($tmp, FEED_CACHE_FILE);
} else {
    feed_log('ПРЕДУПРЕЖДЕНИЕ: не удалось обновить кэш ' . FEED_CACHE_FILE);
}

/* ── Итоговый лог ── */
$reasonsStr = $skipReasons
    ? ' | причины: ' . implode('; ', array_map(
        static fn($k, $v) => "$k×$v", array_keys($skipReasons), array_values($skipReasons)))
    : '';
feed_log(sprintf(
    'OK: прочитано=%d, в фид=%d, отброшено=%d%s',
    $stats['read'], $stats['valid'], $stats['skipped'], $reasonsStr
));

/* ── Вывод ── */
if ($IS_CLI) {
    // CRON: файл autoru.xml уже записан выше; на stdout ничего лишнего.
    fwrite(STDERR, sprintf(
        "feed.php: записан %s (в фид %d из %d)\n",
        FEED_CACHE_FILE, $stats['valid'], $stats['read']
    ));
    exit(0);
}

header('Content-Type: application/xml; charset=utf-8');
header('Content-Length: ' . strlen($xml));
echo $xml;
} // feed_main()

/* Запуск (в тестах отключается через define('FEED_NO_MAIN', true)). */
if (!defined('FEED_NO_MAIN')) {
    feed_main();
}
