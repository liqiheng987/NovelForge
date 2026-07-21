from contextlib import closing
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from tools import generate_tags


MODES = {"guided", "collaborative", "silent", "traceable", "teaching"}
RULE_CATEGORIES = {"character", "world", "plot", "system"}
FACT_CATEGORIES = RULE_CATEGORIES
IMPACT_RELATIONS = {"causal", "foreshadow", "reference"}
IMPACT_ACTIONS = {"review", "rewrite", "none"}
MATERIAL_TYPE_LABELS = {
    "fantasy": "奇幻",
    "scifi": "科幻",
    "wuxia": "武侠",
    "mystery": "推理 / 悬疑",
    "romance": "爱情",
    "historical": "历史",
    "horror": "恐怖",
    "thriller": "惊悚",
    "western": "西部",
    "stream_of_consciousness": "意识流",
    "epistolary": "书信体",
    "autobiographical": "自传体",
    "allegory": "寓言",
    "epic_myth": "史诗神话",
    "experimental": "实验小说",
    "postmodern": "后现代",
    "web_novel": "网络小说",
    "light_novel": "轻小说",
    "fanfiction": "同人",
    "danmei": "耽美",
    "isekai": "异世界",
    "dungeon_core": "地下城核心",
    "revenge": "复仇流",
    "rebirth": "重生流",
    "system": "系统流",
    "progression": "成长升级流",
    "invincible": "无敌流",
}
MATERIAL_SOURCE_LABELS = {"model": "Agent 自动识别", "user_hint": "用户指定"}
MATERIAL_NODE_LABELS = {
    "meta": "作品信息",
    "character": "人物素材",
    "worldview": "世界观素材",
    "plot": "情节素材",
    "theme": "主题素材",
    "field": "写作技法",
}
MATERIAL_CONTEXT_LIMIT = 150000
CHAPTER_CONTEXT_LIMIT = 150000
CHAPTER_SUMMARY_LIMIT = 1200
CHAPTER_VERSION_RETENTION = 50
BACKUP_RETENTION = 7
CHAPTER_MEMORY_LIST_FIELDS = (
    "key_events",
    "character_changes",
    "unresolved_threads",
    "resolved_threads",
    "timeline",
    "locations",
    "continuity_notes",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    configured = os.environ.get("NOVELFORGE_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys_platform() == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "NovelForge" / "storage" / "novel_forge.db"


def sys_platform() -> str:
    import sys

    return sys.platform


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and not os.environ.get("NOVELFORGE_DB_PATH", "").strip():
        candidates = [Path(__file__).parent / "data" / "novelforge.db"]
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            candidates.insert(0, Path(os.environ["LOCALAPPDATA"]) / "NovelForge" / "data" / "novelforge.db")
        legacy = next((candidate for candidate in candidates if candidate.is_file()), None)
        if legacy:
            shutil.copy2(legacy, path)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def backup_directory() -> Path:
    return database_path().parent / "backups"


def list_database_backups() -> list[dict[str, Any]]:
    directory = backup_directory()
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("novel_forge-*.db"), reverse=True):
        try:
            metadata = path.stat()
        except OSError:
            continue
        records.append(
            {
                "path": str(path),
                "name": path.name,
                "size": metadata.st_size,
                "created_at": datetime.fromtimestamp(metadata.st_mtime, timezone.utc).isoformat(),
            }
        )
    return records


def create_database_backup(force: bool = True) -> dict[str, Any]:
    source_path = database_path()
    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise ValueError("数据库尚未创建，暂时无法备份")
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    daily_prefix = f"novel_forge-{timestamp:%Y%m%d}-"
    existing = next(iter(sorted(directory.glob(f"{daily_prefix}*.db"), reverse=True)), None)
    if existing and not force:
        return next(record for record in list_database_backups() if record["path"] == str(existing))
    target = directory / f"{daily_prefix}{timestamp:%H%M%S}.db"
    temporary = target.with_suffix(".tmp")
    try:
        with closing(connect()) as source, closing(sqlite3.connect(temporary)) as destination:
            source.backup(destination)
            check = destination.execute("PRAGMA quick_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise ValueError("备份完整性检查失败")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    backups = list_database_backups()
    for stale in backups[BACKUP_RETENTION:]:
        Path(stale["path"]).unlink(missing_ok=True)
    return next(record for record in list_database_backups() if record["path"] == str(target))


def database_status() -> dict[str, Any]:
    with closing(connect()) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    backups = list_database_backups()
    path = database_path()
    return {
        "status": "ok" if result and str(result[0]).lower() == "ok" else "error",
        "database_path": str(path),
        "database_size": path.stat().st_size if path.is_file() else 0,
        "backup_count": len(backups),
        "latest_backup": backups[0] if backups else None,
    }


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_project_row(connection: sqlite3.Connection, title: str, project_id: str | None = None) -> str:
    project_id = project_id or str(uuid4())
    timestamp = now()
    connection.execute(
        "INSERT INTO projects (id,title,created_at,updated_at) VALUES (?,?,?,?)",
        (project_id, title.strip() or "未命名作品", timestamp, timestamp),
    )
    return project_id


def migrate_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );

        CREATE TABLE IF NOT EXISTS novels (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            created_at DATETIME NOT NULL
        );

        CREATE TABLE IF NOT EXISTS material_nodes (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            parent_id TEXT,
            node_type TEXT NOT NULL CHECK (node_type IN ('collection','meta','character','worldview','plot','theme','field')),
            category TEXT NOT NULL,
            display_name TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES material_nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS material_source_segments (
            material_id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            FOREIGN KEY (material_id) REFERENCES material_nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS novel_chapter_cards (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            summary TEXT NOT NULL,
            card TEXT NOT NULL,
            importance INTEGER NOT NULL,
            confidence REAL NOT NULL,
            refined INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            UNIQUE(novel_id, chapter_index),
            FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            last_accessed DATETIME NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content TEXT NOT NULL,
            selected_material_ids TEXT NOT NULL,
            has_paper INTEGER NOT NULL CHECK (has_paper IN (0,1)),
            created_at DATETIME NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chapters (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapter_versions (
            id TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            session_id TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            memory TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('edit','ai_edit','restore','delete')),
            chapter_created_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            restored_at DATETIME,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chapter_drafts (
            chapter_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_updated_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pinned_materials (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            material_id TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES material_nodes(id) ON DELETE CASCADE,
            UNIQUE(project_id, material_id)
        );

        CREATE TABLE IF NOT EXISTS universe_rules (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            immutable INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS fact_tables (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE(project_id, category, key),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS impact_logs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            changed_node_id TEXT NOT NULL,
            change_type TEXT NOT NULL,
            affected_node_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            action_required TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS story_nodes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            parent_id TEXT,
            layer TEXT NOT NULL CHECK (layer IN ('premise','volume_outline','chapter_beat','content','attachment')),
            node_type TEXT NOT NULL DEFAULT 'note',
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            locked INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL,
            FOREIGN KEY (parent_id) REFERENCES story_nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME NOT NULL
        );
        """
    )
    add_column(connection, "sessions", "project_id", "TEXT")
    add_column(connection, "sessions", "branch_of", "TEXT")
    add_column(connection, "sessions", "branch_name", "TEXT")
    add_column(connection, "sessions", "mode", "TEXT DEFAULT 'guided'")
    add_column(connection, "chapters", "project_id", "TEXT")
    add_column(connection, "chapters", "summary", "TEXT DEFAULT ''")
    add_column(connection, "chapters", "memory", "TEXT DEFAULT '{}'")
    add_column(connection, "projects", "status", "TEXT DEFAULT 'active'")
    add_column(connection, "projects", "settings", "TEXT DEFAULT '{}'")

    sessions = connection.execute(
        "SELECT id,title,created_at,last_accessed,project_id,mode,branch_name FROM sessions"
    ).fetchall()
    for row in sessions:
        project_id = row["project_id"]
        if not project_id or not connection.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone():
            project_id = create_project_row(connection, str(row["title"] or "未命名作品"))
            connection.execute(
                "UPDATE sessions SET project_id=?,mode=?,branch_name=? WHERE id=?",
                (project_id, str(row["mode"] or "guided"), str(row["branch_name"] or "主分支"), row["id"]),
            )
        elif not row["mode"] or not row["branch_name"]:
            connection.execute(
                "UPDATE sessions SET mode=COALESCE(mode,'guided'),branch_name=COALESCE(branch_name,'主分支') WHERE id=?",
                (row["id"],),
            )
    connection.execute(
        """
        UPDATE chapters
        SET project_id=(SELECT project_id FROM sessions WHERE sessions.id=chapters.session_id)
        WHERE project_id IS NULL
        """
    )
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_material_nodes_novel_parent ON material_nodes(novel_id,parent_id,sort_order);
        CREATE INDEX IF NOT EXISTS idx_chapter_cards_novel_order ON novel_chapter_cards(novel_id,chapter_index);
        CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id,last_accessed);
        CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_chapters_project_order ON chapters(project_id,sort_order);
        CREATE INDEX IF NOT EXISTS idx_chapter_versions_chapter ON chapter_versions(chapter_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_chapter_versions_trash ON chapter_versions(project_id,event_type,restored_at,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_chapter_drafts_project ON chapter_drafts(project_id,updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pinned_project ON pinned_materials(project_id,priority);
        CREATE INDEX IF NOT EXISTS idx_universe_project ON universe_rules(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_fact_project_category ON fact_tables(project_id,category);
        CREATE INDEX IF NOT EXISTS idx_impact_project ON impact_logs(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_story_project_layer ON story_nodes(project_id,layer,sort_order);
        CREATE INDEX IF NOT EXISTS idx_story_parent ON story_nodes(parent_id,sort_order);
        """
    )


def initialize_database() -> None:
    with closing(connect()) as connection:
        with connection:
            migrate_schema(connection)
            ensure_session(connection)
            normalize_chapter_titles(connection)
            compact_material_source_metadata(connection)
    if not os.environ.get("NOVELFORGE_DB_PATH", "").strip():
        create_database_backup(force=False)


def set_app_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO app_state (key,value,updated_at) VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
        """,
        (key, value, now()),
    )


def get_app_state(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def ensure_session(connection: sqlite3.Connection) -> str:
    state = get_app_state(connection, "current_session_id")
    if state and connection.execute("SELECT 1 FROM sessions WHERE id=?", (state,)).fetchone():
        return state
    row = connection.execute(
        "SELECT id FROM sessions ORDER BY last_accessed DESC LIMIT 1"
    ).fetchone()
    if row:
        session_id = str(row[0])
    else:
        project_id = create_project_row(connection, "未命名作品")
        session_id = str(uuid4())
        timestamp = now()
        connection.execute(
            """
            INSERT INTO sessions (id,project_id,branch_of,branch_name,mode,title,created_at,last_accessed)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (session_id, project_id, None, "主分支", "guided", "主会话", timestamp, timestamp),
        )
    set_app_state(connection, "current_session_id", session_id)
    project = connection.execute("SELECT project_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if project:
        set_app_state(connection, "current_project_id", str(project[0]))
    return session_id


def normalize_chapter_titles(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT id,title FROM chapters").fetchall():
        normalized = re.sub(r"^第\s*\d+\s*章[\s·:：—-]*", "", str(row["title"])).strip()
        if normalized and normalized != row["title"]:
            connection.execute("UPDATE chapters SET title=?,updated_at=? WHERE id=?", (normalized, now(), row["id"]))


def compact_material_source_metadata(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id,content FROM material_nodes WHERE content LIKE '%source_ranges%' OR content LIKE '%\"chapters\"%' OR content LIKE '%ordered_events%'"
    ).fetchall()
    for row in rows:
        try:
            content = json.loads(str(row["content"] or "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        details = content.get("details") if isinstance(content, dict) else None
        if not isinstance(details, dict):
            continue
        changed = False
        source_ranges = details.get("source_ranges") if isinstance(details, dict) else None
        if isinstance(source_ranges, list):
            compacted: list[Any] = []
            for source in source_ranges:
                if not isinstance(source, dict):
                    compacted.append(source)
                    continue
                headings = source.get("chapter_headings")
                if isinstance(headings, list):
                    source = {key: value for key, value in source.items() if key != "chapter_headings"}
                    source.update(
                        {
                            "chapter_title_start": str(headings[0]) if headings else "",
                            "chapter_title_end": str(headings[-1]) if headings else "",
                            "chapter_count": len(headings),
                        }
                    )
                    changed = True
                compacted.append(source)
            details["source_ranges"] = compacted
        chapters = details.get("chapters")
        if isinstance(chapters, list):
            details["chapter_count"] = len(chapters)
            details["storage"] = "novel_chapter_cards"
            details["highlights"] = [
                {"index": card.get("index"), "title": card.get("title"), "summary": card.get("summary")}
                for card in chapters
                if isinstance(card, dict) and (card.get("refined") or int(card.get("importance") or 0) >= 8)
            ][:8]
            details.pop("chapters", None)
            changed = True
        ordered_events = details.get("ordered_events")
        if isinstance(ordered_events, list) and len(ordered_events) > 12:
            positions = sorted({round((len(ordered_events) - 1) * index / 11) for index in range(12)})
            selected_events = [ordered_events[position] for position in positions]
            details["ordered_events"] = selected_events
            details["source_ranges"] = [
                {"chapter_index": event.get("chapter_index"), "title": event.get("title")}
                for event in selected_events if isinstance(event, dict)
            ]
            changed = True
        if changed:
            connection.execute("UPDATE material_nodes SET content=? WHERE id=?", (json.dumps(content, ensure_ascii=False), row["id"]))


def project_record(row: sqlite3.Row, active_id: str | None = None) -> dict[str, Any]:
    record = dict(row)
    try:
        record["settings"] = json.loads(record.get("settings") or "{}")
    except (json.JSONDecodeError, TypeError):
        record["settings"] = {}
    record["active"] = str(record["id"]) == active_id if active_id else False
    return record


def session_record(row: sqlite3.Row, active_id: str | None = None) -> dict[str, Any]:
    record = dict(row)
    record["active"] = str(record["id"]) == active_id if active_id else False
    return record


def list_projects(include_archived: bool = True) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        current_session = ensure_session(connection)
        current_project = connection.execute("SELECT project_id FROM sessions WHERE id=?", (current_session,)).fetchone()
        active_id = str(current_project[0]) if current_project else None
        query = "SELECT * FROM projects"
        if not include_archived:
            query += " WHERE status='active' OR status IS NULL"
        rows = connection.execute(f"{query} ORDER BY updated_at DESC,created_at DESC").fetchall()
        connection.commit()
    return [project_record(row, active_id) for row in rows]


def create_project(title: str, mode: str = "guided") -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("不支持的创作模式")
    project_id = str(uuid4())
    session_id = str(uuid4())
    timestamp = now()
    with closing(connect()) as connection:
        with connection:
            create_project_row(connection, title, project_id)
            connection.execute(
                """
                INSERT INTO sessions (id,project_id,branch_of,branch_name,mode,title,created_at,last_accessed)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (session_id, project_id, None, "主分支", mode, "主会话", timestamp, timestamp),
            )
            set_app_state(connection, "current_project_id", project_id)
            set_app_state(connection, "current_session_id", session_id)
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return {**project_record(row, project_id), "session_id": session_id, "mode": mode}


def rename_project(project_id: str, title: str) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("作品名称不能为空")
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?", (title, now(), project_id))
            if cursor.rowcount == 0:
                raise ValueError("作品不存在")
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return project_record(row)


def update_project_settings(project_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise ValueError("作品不存在")
            try:
                current = json.loads(row["settings"] or "{}")
            except (json.JSONDecodeError, TypeError):
                current = {}
            current.update(settings)
            connection.execute("UPDATE projects SET settings=?,updated_at=? WHERE id=?", (json.dumps(current, ensure_ascii=False), now(), project_id))
            updated = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return project_record(updated)


def set_project_status(project_id: str, status: str) -> dict[str, Any]:
    if status not in {"active", "archived"}:
        raise ValueError("不支持的作品状态")
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (status, now(), project_id))
            if cursor.rowcount == 0:
                raise ValueError("作品不存在")
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return project_record(row)


def delete_project(project_id: str) -> str:
    with closing(connect()) as connection:
        with connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("作品不存在")
            if connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] <= 1:
                raise ValueError("至少保留一个作品")
            session_ids = [str(row[0]) for row in connection.execute("SELECT id FROM sessions WHERE project_id=?", (project_id,))]
            if session_ids:
                placeholders = ",".join("?" for _ in session_ids)
                connection.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", session_ids)
            connection.execute("DELETE FROM chapters WHERE project_id=?", (project_id,))
            connection.execute("DELETE FROM sessions WHERE project_id=?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            fallback = connection.execute("SELECT id,project_id FROM sessions ORDER BY last_accessed DESC LIMIT 1").fetchone()
            if not fallback:
                fallback_project = connection.execute("SELECT id FROM projects ORDER BY updated_at DESC LIMIT 1").fetchone()
                if not fallback_project:
                    fallback_project_id = create_project_row(connection, "未命名作品")
                else:
                    fallback_project_id = str(fallback_project[0])
                timestamp = now()
                fallback_id = str(uuid4())
                connection.execute(
                    "INSERT INTO sessions (id,project_id,branch_name,mode,title,created_at,last_accessed) VALUES (?,?,?,?,?,?,?)",
                    (fallback_id, fallback_project_id, "主分支", "guided", "主会话", timestamp, timestamp),
                )
                fallback = {"id": fallback_id, "project_id": fallback_project_id}
            set_app_state(connection, "current_session_id", str(fallback["id"]))
            set_app_state(connection, "current_project_id", str(fallback["project_id"]))
    return str(fallback["id"])


def list_sessions(project_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        current = ensure_session(connection)
        if project_id:
            rows = connection.execute("SELECT * FROM sessions WHERE project_id=? ORDER BY last_accessed DESC", (project_id,)).fetchall()
        else:
            rows = connection.execute("SELECT * FROM sessions ORDER BY last_accessed DESC").fetchall()
        connection.commit()
    return [session_record(row, current) for row in rows]


def create_session(
    project_id: str,
    title: str = "新会话",
    mode: str = "guided",
    branch_of: str | None = None,
    branch_name: str = "主分支",
) -> dict[str, Any]:
    with closing(connect()) as probe:
        project_exists = probe.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project_exists:
        if title == "新会话" and mode == "guided" and branch_of is None and branch_name == "主分支":
            title, mode = project_id, "guided"
            project_id = create_project(title, mode)["id"]
        else:
            raise ValueError("作品不存在")
    if mode not in MODES:
        raise ValueError("不支持的创作模式")
    session_id = str(uuid4())
    timestamp = now()
    with closing(connect()) as connection:
        with connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("作品不存在")
            connection.execute(
                "INSERT INTO sessions (id,project_id,branch_of,branch_name,mode,title,created_at,last_accessed) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, project_id, branch_of, branch_name.strip() or "主分支", mode, title.strip() or "新会话", timestamp, timestamp),
            )
            set_app_state(connection, "current_project_id", project_id)
            set_app_state(connection, "current_session_id", session_id)
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return session_record(row, session_id)


def switch_session(session_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise ValueError("会话不存在")
            timestamp = now()
            connection.execute("UPDATE sessions SET last_accessed=? WHERE id=?", (timestamp, session_id))
            set_app_state(connection, "current_session_id", session_id)
            set_app_state(connection, "current_project_id", str(row["project_id"]))
    return {
        "session": {**dict(row), "last_accessed": timestamp, "active": True},
        "project_id": str(row["project_id"]),
        "messages": list_messages(session_id),
    }


def switch_project(project_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        session = connection.execute(
            "SELECT id FROM sessions WHERE project_id=? ORDER BY last_accessed DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if not session:
        raise ValueError("作品不存在或没有可用会话")
    result = switch_session(str(session[0]))
    result["project"] = next(project for project in list_projects() if project["id"] == project_id)
    return result


def update_session_mode(session_id: str, mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("不支持的创作模式")
    with closing(connect()) as connection:
        with connection:
            cursor = connection.execute(
                "UPDATE sessions SET mode=?,last_accessed=? WHERE id=?",
                (mode, now(), session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("会话不存在")
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return session_record(row, session_id)


def delete_session(session_id: str) -> str:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT project_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise ValueError("会话不存在")
            fallback = connection.execute(
                "SELECT id,project_id FROM sessions WHERE id<>? AND project_id=? ORDER BY last_accessed DESC LIMIT 1",
                (session_id, row["project_id"]),
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            if not connection.execute("SELECT 1 FROM sessions WHERE project_id=?", (row["project_id"],)).fetchone():
                timestamp = now()
                replacement_id = str(uuid4())
                connection.execute(
                    "INSERT INTO sessions (id,project_id,branch_name,mode,title,created_at,last_accessed) VALUES (?,?,?,?,?,?,?)",
                    (replacement_id, row["project_id"], "主分支", "guided", "主会话", timestamp, timestamp),
                )
                fallback = {"id": replacement_id, "project_id": row["project_id"]}
            if not fallback:
                fallback = connection.execute(
                    "SELECT id,project_id FROM sessions ORDER BY last_accessed DESC LIMIT 1"
                ).fetchone()
            set_app_state(connection, "current_session_id", str(fallback["id"]))
            set_app_state(connection, "current_project_id", str(fallback["project_id"]))
    return str(fallback["id"])


def create_branch(project_id: str, source_session_id: str, name: str, description: str | None = None) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("分支名称不能为空")
    with closing(connect()) as connection:
        with connection:
            source = connection.execute("SELECT * FROM sessions WHERE id=? AND project_id=?", (source_session_id, project_id)).fetchone()
            if not source:
                raise ValueError("源会话不存在或不属于当前作品")
            if connection.execute("SELECT COUNT(*) FROM sessions WHERE project_id=?", (project_id,)).fetchone()[0] >= 20:
                raise ValueError("分支数量已达上限(20个)")
            if connection.execute("SELECT 1 FROM sessions WHERE project_id=? AND branch_name=?", (project_id, name)).fetchone():
                raise ValueError("分支名称已存在")
            branch_id = str(uuid4())
            timestamp = now()
            connection.execute(
                "INSERT INTO sessions (id,project_id,branch_of,branch_name,mode,title,created_at,last_accessed) VALUES (?,?,?,?,?,?,?,?)",
                (branch_id, project_id, source_session_id, name, source["mode"] or "guided", description or f"{name} 会话", timestamp, timestamp),
            )
            messages = connection.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (source_session_id,)).fetchall()
            for message in messages:
                connection.execute(
                    "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                    (str(uuid4()), branch_id, message["role"], message["content"], message["selected_material_ids"], message["has_paper"], message["created_at"]),
                )
            set_app_state(connection, "current_session_id", branch_id)
            set_app_state(connection, "current_project_id", project_id)
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (branch_id,)).fetchone()
    return session_record(row, branch_id)


def branch_compare(project_id: str, branch_a_id: str, branch_b_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        sessions = connection.execute("SELECT id FROM sessions WHERE project_id=? AND id IN (?,?)", (project_id, branch_a_id, branch_b_id)).fetchall()
        if len(sessions) != 2:
            raise ValueError("两个分支必须属于同一作品")
        def records(session_id: str) -> list[dict[str, str]]:
            return [
                {"id": str(row["id"]), "content": str(row["content"])}
                for row in connection.execute("SELECT id,content FROM messages WHERE session_id=? AND role='assistant' ORDER BY created_at,id", (session_id,))
            ]
        left = records(branch_a_id)
        right = records(branch_b_id)
    matcher = SequenceMatcher(None, [item["content"] for item in left], [item["content"] for item in right], autojunk=False)
    added: list[str] = []
    deleted: list[str] = []
    modified: list[dict[str, str]] = []
    for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        if operation == "insert":
            added.extend(item["id"] for item in right[right_start:right_end])
            continue
        if operation == "delete":
            deleted.extend(item["id"] for item in left[left_start:left_end])
            continue
        left_items = left[left_start:left_end]
        right_items = right[right_start:right_end]
        paired = min(len(left_items), len(right_items))
        modified.extend(
            {"id": right_items[index]["id"], "old": left_items[index]["content"][:500], "new": right_items[index]["content"][:500]}
            for index in range(paired)
        )
        deleted.extend(item["id"] for item in left_items[paired:])
        added.extend(item["id"] for item in right_items[paired:])
    return {"added": added, "deleted": deleted, "modified": modified}


def branch_merge(project_id: str, source_session_id: str, target_session_id: str) -> dict[str, Any]:
    comparison = branch_compare(project_id, source_session_id, target_session_id)
    if comparison["modified"]:
        raise ValueError("分支内容冲突，请先对比并解决")
    with closing(connect()) as connection:
        with connection:
            target_content = {
                (str(row["role"]), str(row["content"]))
                for row in connection.execute("SELECT role,content FROM messages WHERE session_id=?", (target_session_id,))
            }
            for row in connection.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at,id", (source_session_id,)).fetchall():
                identity = (str(row["role"]), str(row["content"]))
                if identity not in target_content:
                    connection.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (str(uuid4()), target_session_id, row["role"], row["content"], row["selected_material_ids"], row["has_paper"], now()))
                    target_content.add(identity)
    return comparison


def message_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["selected_material_ids"] = json.loads(record.get("selected_material_ids") or "[]")
    record["has_paper"] = bool(record.get("has_paper"))
    record["paper"] = None
    if record["has_paper"]:
        try:
            envelope = json.loads(record["content"])
            record["content"] = str(envelope.get("text") or "")
            record["paper"] = envelope.get("paper")
        except (json.JSONDecodeError, TypeError):
            record["has_paper"] = False
    return record


def list_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?) ORDER BY created_at ASC",
            (session_id, limit),
        ).fetchall()
    return [message_record(row) for row in rows]


def paper_history_context(paper: dict[str, Any]) -> str:
    content = str(paper.get("content") or "")
    memory = normalize_chapter_memory(paper.get("memory"), content)
    status = {"draft": "待确认", "collected": "已收录", "abandoned": "已放弃"}.get(str(paper.get("status")), "未知")
    parts = [f"篇章《{paper.get('title') or '未命名'}》[{status}]", f"全章摘要：{memory['summary']}"]
    for field, label in (
        ("key_events", "关键事件"),
        ("character_changes", "人物变化"),
        ("unresolved_threads", "未解线索"),
        ("continuity_notes", "连续性提醒"),
    ):
        values = memory.get(field) or []
        if values:
            parts.append(f"{label}：{'；'.join(values[:8])}")
    if paper.get("status") == "draft" and content:
        compact = re.sub(r"\s+", " ", content).strip()
        excerpt = compact if len(compact) <= 1200 else f"{compact[:700]}…{compact[-400:]}"
        parts.append(f"待确认稿摘录：{excerpt}")
    return "\n".join(parts)


def chat_history(session_id: str, limit: int = 40, exclude_message_id: str | None = None) -> list[dict[str, str]]:
    messages = list_messages(session_id, limit + (1 if exclude_message_id else 0))
    if exclude_message_id:
        messages = [message for message in messages if message["id"] != exclude_message_id]
    history: list[dict[str, str]] = []
    for message in messages[-limit:]:
        content = message["content"]
        if message.get("paper"):
            content += f"\n\n{paper_history_context(message['paper'])}"
        history.append({"role": message["role"], "content": content})
    return history


def save_user_message(session_id: str, content: str, selected_ids: list[str]) -> dict[str, Any]:
    message_id = str(uuid4())
    timestamp = now()
    with closing(connect()) as connection:
        with connection:
            connection.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (message_id, session_id, "user", content, json.dumps(selected_ids, ensure_ascii=False), 0, timestamp))
            row = connection.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
            title = str(row[0]) if row else "新会话"
            new_title = content.strip().replace("\n", " ")[:24] if title in {"新会话", "新对话", "主会话"} else title
            connection.execute("UPDATE sessions SET title=?,last_accessed=? WHERE id=?", (new_title or title, timestamp, session_id))
    return {"id": message_id, "session_id": session_id, "role": "user", "content": content, "selected_material_ids": selected_ids, "has_paper": False, "paper": None, "created_at": timestamp}


def save_assistant_message(session_id: str, content: str, paper: dict[str, Any] | None = None, message_id: str | None = None) -> dict[str, Any]:
    assistant_id = message_id or str(uuid4())
    timestamp = now()
    stored_content = json.dumps({"text": content, "paper": paper}, ensure_ascii=False) if paper else content
    with closing(connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO messages VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,role='assistant',content=excluded.content,selected_material_ids='[]',has_paper=excluded.has_paper,created_at=excluded.created_at
                """,
                (assistant_id, session_id, "assistant", stored_content, "[]", 1 if paper else 0, timestamp),
            )
    return {"id": assistant_id, "session_id": session_id, "role": "assistant", "content": content, "selected_material_ids": [], "has_paper": bool(paper), "paper": paper, "created_at": timestamp}


def delete_assistant_message(message_id: str, session_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM messages WHERE id=? AND session_id=? AND role='assistant'", (message_id, session_id))


def delete_user_message(message_id: str, session_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM messages WHERE id=? AND session_id=? AND role='user'", (message_id, session_id))


def get_paper(message_id: str) -> dict[str, Any] | None:
    with closing(connect()) as connection:
        row = connection.execute("SELECT * FROM messages WHERE id=? AND has_paper=1", (message_id,)).fetchone()
    return message_record(row).get("paper") if row else None


def update_paper(message_id: str, paper: dict[str, Any]) -> None:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if not row:
                raise ValueError("稿纸不存在")
            record = message_record(row)
            envelope = json.dumps({"text": record["content"], "paper": paper}, ensure_ascii=False)
            connection.execute("UPDATE messages SET content=?,has_paper=1 WHERE id=?", (envelope, message_id))


def get_project_for_session(session_id: str) -> str:
    with closing(connect()) as connection:
        row = connection.execute("SELECT project_id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row or not row[0]:
        raise ValueError("会话没有所属作品")
    return str(row[0])


def list_chapters(project_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        if project_id and not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            session_row = connection.execute("SELECT project_id FROM sessions WHERE id=?", (project_id,)).fetchone()
            if session_row:
                project_id = str(session_row[0])
        if project_id:
            rows = connection.execute("SELECT * FROM chapters WHERE project_id=? ORDER BY sort_order ASC", (project_id,)).fetchall()
        else:
            rows = connection.execute("SELECT * FROM chapters ORDER BY project_id,sort_order ASC").fetchall()
    return [chapter_record(row) for row in rows]


def get_chapter_draft(chapter_id: str) -> dict[str, Any] | None:
    with closing(connect()) as connection:
        row = connection.execute(
            "SELECT * FROM chapter_drafts WHERE chapter_id=?", (chapter_id,)
        ).fetchone()
    return dict(row) if row else None


def save_chapter_draft(
    chapter_id: str,
    title: str,
    content: str,
    source_updated_at: str,
) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            chapter = connection.execute(
                "SELECT project_id,updated_at FROM chapters WHERE id=?", (chapter_id,)
            ).fetchone()
            if not chapter:
                raise ValueError("篇章不存在")
            if str(chapter["updated_at"]) != source_updated_at:
                raise ValueError("章节已在其他位置更新，请重新打开后再编辑")
            timestamp = now()
            connection.execute(
                """
                INSERT INTO chapter_drafts (
                    chapter_id,project_id,title,content,source_updated_at,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chapter_id) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    source_updated_at=excluded.source_updated_at,
                    updated_at=excluded.updated_at
                """,
                (
                    chapter_id,
                    chapter["project_id"],
                    title,
                    content,
                    source_updated_at,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM chapter_drafts WHERE chapter_id=?", (chapter_id,)
            ).fetchone()
    return dict(row)


def clear_chapter_draft(chapter_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM chapter_drafts WHERE chapter_id=?", (chapter_id,))


def chapter_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    try:
        memory = json.loads(record.get("memory") or "{}")
    except (json.JSONDecodeError, TypeError):
        memory = {}
    record["memory"] = memory if isinstance(memory, dict) else {}
    return record


def chapter_version_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    try:
        memory = json.loads(record.get("memory") or "{}")
    except (json.JSONDecodeError, TypeError):
        memory = {}
    record["memory"] = memory if isinstance(memory, dict) else {}
    return record


def save_chapter_version(
    connection: sqlite3.Connection,
    chapter: sqlite3.Row,
    event_type: str,
) -> str:
    version_id = str(uuid4())
    connection.execute(
        """
        INSERT INTO chapter_versions (
            id,chapter_id,project_id,session_id,title,content,summary,memory,sort_order,
            event_type,chapter_created_at,created_at,restored_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)
        """,
        (
            version_id,
            chapter["id"],
            chapter["project_id"],
            chapter["session_id"],
            chapter["title"],
            chapter["content"],
            chapter["summary"] or "",
            chapter["memory"] or "{}",
            chapter["sort_order"],
            event_type,
            chapter["created_at"],
            now(),
        ),
    )
    if event_type != "delete":
        stale_versions = connection.execute(
            """
            SELECT id FROM chapter_versions
            WHERE chapter_id=? AND event_type<>'delete'
            ORDER BY created_at DESC, id DESC
            LIMIT -1 OFFSET ?
            """,
            (chapter["id"], CHAPTER_VERSION_RETENTION),
        ).fetchall()
        connection.executemany(
            "DELETE FROM chapter_versions WHERE id=?",
            [(row["id"],) for row in stale_versions],
        )
    return version_id


def list_chapter_versions(chapter_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT * FROM chapter_versions
            WHERE chapter_id=? AND event_type<>'delete'
            ORDER BY created_at DESC, id DESC
            """,
            (chapter_id,),
        ).fetchall()
    return [chapter_version_record(row) for row in rows]


def list_deleted_chapters(project_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise ValueError("作品不存在")
        rows = connection.execute(
            """
            SELECT * FROM chapter_versions
            WHERE project_id=? AND event_type='delete' AND restored_at IS NULL
            ORDER BY created_at DESC, id DESC
            """,
            (project_id,),
        ).fetchall()
    return [chapter_version_record(row) for row in rows]


def restore_chapter_version(version_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            version = connection.execute(
                "SELECT * FROM chapter_versions WHERE id=?", (version_id,)
            ).fetchone()
            if not version:
                raise ValueError("章节版本不存在")
            project_id = str(version["project_id"])
            current = connection.execute(
                "SELECT * FROM chapters WHERE id=?", (version["chapter_id"],)
            ).fetchone()
            restored_from_deleted = str(version["event_type"]) == "delete"
            if restored_from_deleted:
                if version["restored_at"]:
                    raise ValueError("该章节已经恢复")
                if current:
                    raise ValueError("同一章节已经存在")
                session = connection.execute(
                    "SELECT id FROM sessions WHERE id=? AND project_id=?",
                    (version["session_id"], project_id),
                ).fetchone()
                if not session:
                    session = connection.execute(
                        "SELECT id FROM sessions WHERE project_id=? ORDER BY last_accessed DESC LIMIT 1",
                        (project_id,),
                    ).fetchone()
                if not session:
                    raise ValueError("作品没有可用会话，无法恢复章节")
                chapter_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chapters WHERE project_id=?", (project_id,)
                    ).fetchone()[0]
                )
                sort_order = max(1, min(int(version["sort_order"]), chapter_count + 1))
                connection.execute(
                    "UPDATE chapters SET sort_order=sort_order+1 WHERE project_id=? AND sort_order>=?",
                    (project_id, sort_order),
                )
                timestamp = now()
                connection.execute(
                    """
                    INSERT INTO chapters (
                        id,session_id,project_id,title,content,summary,memory,sort_order,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        version["chapter_id"],
                        session["id"],
                        project_id,
                        version["title"],
                        version["content"],
                        version["summary"],
                        version["memory"],
                        sort_order,
                        version["chapter_created_at"],
                        timestamp,
                    ),
                )
                connection.execute(
                    "UPDATE chapter_versions SET restored_at=? WHERE id=?",
                    (timestamp, version_id),
                )
            else:
                if not current:
                    raise ValueError("章节已删除，请先从回收站恢复")
                save_chapter_version(connection, current, "restore")
                connection.execute(
                    """
                    UPDATE chapters
                    SET title=?,content=?,summary=?,memory=?,updated_at=?
                    WHERE id=?
                    """,
                    (
                        version["title"],
                        version["content"],
                        version["summary"],
                        version["memory"],
                        now(),
                        version["chapter_id"],
                    ),
                )
            rebuild_project_chapter_facts(connection, project_id)
            chapter = connection.execute(
                "SELECT * FROM chapters WHERE id=?", (version["chapter_id"],)
            ).fetchone()
    return {
        "chapter": chapter_record(chapter),
        "restored_from_deleted": restored_from_deleted,
    }


def purge_deleted_chapter(version_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            version = connection.execute(
                """
                SELECT * FROM chapter_versions
                WHERE id=? AND event_type='delete' AND restored_at IS NULL
                """,
                (version_id,),
            ).fetchone()
            if not version:
                raise ValueError("回收站记录不存在")
            if connection.execute(
                "SELECT 1 FROM chapters WHERE id=?", (version["chapter_id"],)
            ).fetchone():
                raise ValueError("章节仍然存在，不能永久删除历史")
            connection.execute(
                "DELETE FROM chapter_versions WHERE chapter_id=? AND project_id=?",
                (version["chapter_id"], version["project_id"]),
            )


def fallback_chapter_summary(content: str, limit: int = CHAPTER_SUMMARY_LIMIT) -> str:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(text) <= limit:
        return text
    segment_size = max(80, (limit - 18) // 3)
    middle_start = max(0, (len(text) - segment_size) // 2)
    return (
        f"开篇：{text[:segment_size]}\n"
        f"中段：{text[middle_start:middle_start + segment_size]}\n"
        f"结尾：{text[-segment_size:]}"
    )[:limit]


def normalize_chapter_memory(memory: Any, content: str) -> dict[str, Any]:
    source = memory if isinstance(memory, dict) else {}
    summary = re.sub(r"\s+", " ", str(source.get("summary") or "")).strip()
    if not summary:
        summary = fallback_chapter_summary(content)
    normalized: dict[str, Any] = {"summary": summary[:CHAPTER_SUMMARY_LIMIT]}
    for field in CHAPTER_MEMORY_LIST_FIELDS:
        value = source.get(field)
        if isinstance(value, list):
            items = [re.sub(r"\s+", " ", str(item)).strip()[:400] for item in value]
            normalized[field] = list(dict.fromkeys(item for item in items if item))[:20]
        else:
            normalized[field] = []
    facts: list[dict[str, str]] = []
    raw_facts = source.get("facts") if isinstance(source.get("facts"), list) else []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        key = re.sub(r"\s+", " ", str(item.get("key") or "")).strip()[:120]
        value = re.sub(r"\s+", " ", str(item.get("value") or "")).strip()[:2000]
        if category in FACT_CATEGORIES and key and value:
            facts.append({"category": category, "key": key, "value": value})
    normalized["facts"] = facts[:30]
    return normalized


def chapter_memory_context(chapter: dict[str, Any], body_limit: int) -> str:
    memory = normalize_chapter_memory(chapter.get("memory"), str(chapter.get("content") or ""))
    details: list[str] = [memory["summary"]]
    labels = {
        "key_events": "关键事件",
        "character_changes": "人物变化",
        "unresolved_threads": "未解线索",
        "resolved_threads": "已解线索",
        "timeline": "时间线",
        "locations": "地点",
        "continuity_notes": "连续性提醒",
    }
    for field, label in labels.items():
        values = memory.get(field) or []
        if values:
            details.append(f"{label}：{'；'.join(values)}")
    text = "\n".join(details)
    if len(text) <= body_limit:
        return text
    if body_limit <= 12:
        return text[:body_limit]
    return f"{text[:body_limit - 3].rstrip()}..."


def chapter_summaries(project_id: str) -> str:
    chapters = list_chapters(project_id)
    if not chapters:
        return ""
    header = "[全书分层记忆索引：每个已收录篇章均参与，摘要覆盖全章而非只取开头]"
    prefix_budget = max(12, (CHAPTER_CONTEXT_LIMIT - len(header)) // max(1, len(chapters) * 2))
    prefixes: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        base = f"第{index}章"
        title_limit = max(0, min(60, prefix_budget - len(base) - 3))
        title = str(chapter["title"])
        if len(title) > title_limit:
            title = f"{title[:max(0, title_limit - 1)]}…" if title_limit else ""
        prefixes.append(f"{base}《{title}》：")
    available = max(0, CHAPTER_CONTEXT_LIMIT - len(header) - sum(len(prefix) + 1 for prefix in prefixes))
    per_chapter = available // len(chapters)
    entries = [
        f"{prefix}{chapter_memory_context(chapter, per_chapter)}"
        for prefix, chapter in zip(prefixes, chapters)
    ]
    return "\n".join([header, *entries])[:CHAPTER_CONTEXT_LIMIT]


def rebuild_project_chapter_facts(connection: sqlite3.Connection, project_id: str) -> None:
    connection.execute(
        "DELETE FROM fact_tables WHERE project_id=? AND source LIKE 'chapter:%'",
        (project_id,),
    )
    chapters = connection.execute(
        "SELECT id,content,memory FROM chapters WHERE project_id=? ORDER BY sort_order,created_at",
        (project_id,),
    ).fetchall()
    for chapter in chapters:
        try:
            stored_memory = json.loads(str(chapter["memory"] or "{}"))
        except (json.JSONDecodeError, TypeError):
            stored_memory = {}
        facts = normalize_chapter_memory(stored_memory, str(chapter["content"]))["facts"]
        for fact in facts:
            existing = connection.execute(
                "SELECT source FROM fact_tables WHERE project_id=? AND category=? AND key=?",
                (project_id, fact["category"], fact["key"]),
            ).fetchone()
            if existing and not str(existing["source"]).startswith("chapter:"):
                continue
            if not existing and connection.execute(
                "SELECT COUNT(*) FROM fact_tables WHERE project_id=?", (project_id,)
            ).fetchone()[0] >= 200:
                continue
            timestamp = now()
            connection.execute(
                """
                INSERT INTO fact_tables (id,project_id,category,key,value,source,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id,category,key)
                DO UPDATE SET value=excluded.value,source=excluded.source,updated_at=excluded.updated_at
                """,
                (
                    str(uuid4()),
                    project_id,
                    fact["category"],
                    fact["key"],
                    fact["value"],
                    f"chapter:{chapter['id']}",
                    timestamp,
                    timestamp,
                ),
            )


def confirm_paper(message_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM messages WHERE id=? AND has_paper=1", (message_id,)).fetchone()
            if not row:
                raise ValueError("稿纸不存在")
            record = message_record(row)
            paper = record["paper"]
            if not paper or paper.get("status") == "abandoned":
                raise ValueError("已放弃的稿纸不能收录")
            project_id = str(connection.execute("SELECT project_id FROM sessions WHERE id=?", (record["session_id"],)).fetchone()[0])
            target_id = paper.get("target_chapter_id") or paper.get("chapter_id")
            timestamp = now()
            chapter_memory = normalize_chapter_memory(paper.get("memory"), str(paper["content"]))
            memory_json = json.dumps(chapter_memory, ensure_ascii=False)
            target = connection.execute("SELECT * FROM chapters WHERE id=? AND project_id=?", (target_id, project_id)).fetchone() if target_id else None
            if target:
                normalized = re.sub(r"\s+", "", str(paper["content"]))
                other_chapters = connection.execute("SELECT id,content FROM chapters WHERE project_id=? AND id<>?", (project_id, target["id"])).fetchall()
                if any(SequenceMatcher(None, normalized, re.sub(r"\s+", "", str(other["content"]))).ratio() >= 0.82 for other in other_chapters):
                    raise ValueError("修改稿与其他已收录篇章高度重复，请重新生成")
                if (
                    str(target["title"]) != str(paper["title"])
                    or str(target["content"]) != str(paper["content"])
                    or str(target["memory"] or "{}") != memory_json
                ):
                    save_chapter_version(connection, target, "ai_edit")
                connection.execute("UPDATE chapters SET title=?,content=?,summary=?,memory=?,updated_at=? WHERE id=?", (paper["title"], paper["content"], chapter_memory["summary"], memory_json, timestamp, target["id"]))
                chapter_id = str(target["id"])
            else:
                chapter_id = str(uuid4())
                sort_order = int(connection.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM chapters WHERE project_id=?", (project_id,)).fetchone()[0])
                connection.execute("INSERT INTO chapters (id,session_id,project_id,title,content,summary,memory,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (chapter_id, record["session_id"], project_id, paper["title"], paper["content"], chapter_memory["summary"], memory_json, sort_order, timestamp, timestamp))
            rebuild_project_chapter_facts(connection, project_id)
            paper.update({"status": "collected", "chapter_id": chapter_id, "memory": chapter_memory})
            connection.execute("UPDATE messages SET content=? WHERE id=?", (json.dumps({"text": record["content"], "paper": paper}, ensure_ascii=False), message_id))
            chapter = connection.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    return {
        "chapter": chapter_record(chapter),
        "paper": paper,
        "chapter_operation": "updated" if target else "inserted",
    }


def abandon_paper(message_id: str) -> dict[str, Any]:
    paper = get_paper(message_id)
    if not paper:
        raise ValueError("稿纸不存在")
    if paper.get("status") == "collected":
        raise ValueError("已收录稿纸不能放弃")
    paper["status"] = "abandoned"
    update_paper(message_id, paper)
    return paper


def edit_chapter(chapter_id: str, title: str, content: str) -> dict[str, Any]:
    title = title.strip()
    content = content.strip()
    if not title or not content:
        raise ValueError("篇章标题和正文不能为空")
    timestamp = now()
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
            if not row:
                raise ValueError("篇章不存在")
            if str(row["title"]) == title and str(row["content"]) == content:
                connection.execute("DELETE FROM chapter_drafts WHERE chapter_id=?", (chapter_id,))
                return chapter_record(row)
            save_chapter_version(connection, row, "edit")
            fallback_memory = normalize_chapter_memory({}, content)
            connection.execute(
                "UPDATE chapters SET title=?,content=?,summary=?,memory=?,updated_at=? WHERE id=?",
                (title, content, fallback_memory["summary"], json.dumps(fallback_memory, ensure_ascii=False), timestamp, chapter_id),
            )
            connection.execute("DELETE FROM chapter_drafts WHERE chapter_id=?", (chapter_id,))
            rebuild_project_chapter_facts(connection, str(row["project_id"]))
            row = connection.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    return chapter_record(row)


def get_chapter_project(chapter_id: str) -> str:
    with closing(connect()) as connection:
        row = connection.execute("SELECT project_id FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not row:
        raise ValueError("篇章不存在")
    return str(row["project_id"])


def delete_chapter(chapter_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
            if not row:
                raise ValueError("篇章不存在")
            project_id = str(row["project_id"])
            save_chapter_version(connection, row, "delete")
            connection.execute("DELETE FROM chapters WHERE id=?", (chapter_id,))
            connection.execute("DELETE FROM impact_logs WHERE affected_node_id=?", (chapter_id,))
            rebuild_project_chapter_facts(connection, project_id)
            rows = connection.execute("SELECT id FROM chapters WHERE project_id=? ORDER BY sort_order", (project_id,)).fetchall()
            for index, chapter in enumerate(rows, start=1):
                connection.execute("UPDATE chapters SET sort_order=?,updated_at=? WHERE id=?", (index, now(), chapter[0]))


def reorder_chapters(project_id: str | list[str], chapter_ids: list[str] | None = None) -> list[dict[str, Any]]:
    if chapter_ids is None:
        chapter_ids = list(project_id) if isinstance(project_id, list) else []
        if not chapter_ids:
            return []
        with closing(connect()) as probe:
            row = probe.execute("SELECT project_id FROM chapters WHERE id=?", (chapter_ids[0],)).fetchone()
        if not row:
            raise ValueError("篇章不存在")
        project_id = str(row[0])
    assert isinstance(project_id, str)
    if not chapter_ids:
        return []
    with closing(connect()) as connection:
        with connection:
            existing = {str(row[0]) for row in connection.execute("SELECT id FROM chapters WHERE project_id=?", (project_id,))}
            if set(chapter_ids) != existing or len(chapter_ids) != len(existing):
                raise ValueError("篇章顺序不完整")
            for index, chapter_id in enumerate(chapter_ids, start=1):
                connection.execute("UPDATE chapters SET sort_order=?,updated_at=? WHERE id=? AND project_id=?", (index, now(), chapter_id, project_id))
    return list_chapters(project_id)


def readable_material_content(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value.strip()
    if parsed in ({}, []):
        return ""
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def analysis_material_tags(analysis: dict[str, Any]) -> list[str]:
    tags = ["作品类型"]
    primary_type = str(analysis.get("primary_type") or "").strip()
    secondary_type = str(analysis.get("secondary_type") or "").strip()
    if primary_type:
        tags.append(MATERIAL_TYPE_LABELS.get(primary_type, primary_type))
    if secondary_type:
        tags.append(MATERIAL_TYPE_LABELS.get(secondary_type, secondary_type))
    else:
        source = str(analysis.get("type_source") or "model").strip()
        tags.append(MATERIAL_SOURCE_LABELS.get(source, source))
    return list(dict.fromkeys(tag for tag in tags if tag))[:3]


def item_material_tags(item: dict[str, Any]) -> list[str]:
    supplied = item.get("tags")
    if isinstance(supplied, list):
        tags = list(dict.fromkeys(str(tag).strip() for tag in supplied if str(tag).strip()))[:3]
        if tags:
            return tags
    source = json.dumps(
        {key: item.get(key) for key in ("name", "category", "summary", "details")},
        ensure_ascii=False,
    )
    return generate_tags(source)[:3]


def whole_novel_material_context(title: str, rows: list[sqlite3.Row], limit: int = MATERIAL_CONTEXT_LIMIT) -> str:
    header = f"[整本素材：《{title}》]\n以下内容是该小说经 Agent 分类后的整本分析档案，可整体参考，也可另选具体节点补充细节。"
    parts = [header]
    used = len(header)
    eligible = [
        row
        for row in rows
        if str(row["node_type"]) != "collection"
        and str(row["dimension_name"] or "") != "全文覆盖索引"
        and str(row["category"] or "") != "全文覆盖索引"
    ]
    included = 0
    for row in eligible:
        node_type = MATERIAL_NODE_LABELS.get(str(row["node_type"]), "创作素材")
        category = str(row["category"] or "").strip()
        if category == "type":
            category = "作品类型"
        dimension = str(row["dimension_name"] or "").strip()
        summary = re.sub(r"\s+", " ", str(row["summary"] or "")).strip()
        content = readable_material_content(str(row["content"] or ""))
        compact_content = re.sub(r"\s+", " ", content).strip()
        detail = compact_content if compact_content and compact_content not in summary else ""
        entry = f"- [{dimension or node_type}{f' / {category}' if category and category != dimension else ''}] {row['display_name']}：{summary}"
        if detail:
            entry = f"{entry}；细节：{detail}"
        if used + len(entry) + 1 > limit:
            break
        parts.append(entry)
        used += len(entry) + 1
        included += 1
    if included < len(eligible):
        parts.append(f"- 其余 {len(eligible) - included} 条素材已省略；需要精确细节时可展开并单独勾选对应分类或节点。")
    return "\n".join(parts)


def bounded_material_context(parts: list[str], limit: int = MATERIAL_CONTEXT_LIMIT) -> str:
    included: list[str] = []
    used = 0
    omitted = 0
    for index, part in enumerate(parts):
        value = part.strip()
        if not value:
            continue
        separator = 2 if included else 0
        if used + separator + len(value) <= limit:
            included.append(value)
            used += separator + len(value)
            continue
        remaining = limit - used - separator
        truncated = remaining >= 240
        if truncated:
            suffix = "\n[该素材内容因本轮上下文预算被截断，可在下一轮继续单独引用。]"
            included.append(f"{value[:max(0, remaining - len(suffix))].rstrip()}{suffix}")
        omitted = len(parts) - index - (1 if truncated else 0)
        break
    if omitted:
        notice = f"[另有 {omitted} 个素材片段未在本轮完整展开；所有选择 ID 已保存，可分轮继续使用。]"
        current = "\n\n".join(included)
        if len(current) + 2 + len(notice) <= limit:
            included.append(notice)
    return "\n\n".join(included)


def material_context(ids: list[str]) -> str:
    if not ids:
        return ""
    unique_ids = list(dict.fromkeys(str(item).strip() for item in ids if str(item).strip()))
    if not unique_ids:
        return ""
    with closing(connect()) as connection:
        connection.execute("CREATE TEMP TABLE selected_material_input (id TEXT PRIMARY KEY, position INTEGER NOT NULL)")
        connection.executemany(
            "INSERT OR IGNORE INTO selected_material_input (id,position) VALUES (?,?)",
            [(material_id, position) for position, material_id in enumerate(unique_ids)],
        )
        selected_novels = connection.execute(
            "SELECT novel.id,novel.title FROM novels novel JOIN selected_material_input selected ON selected.id=novel.id ORDER BY selected.position",
        ).fetchall()
        selected_novel_ids = {str(row["id"]) for row in selected_novels}
        selected_nodes = connection.execute(
            """
            WITH RECURSIVE selected(id,position) AS (
                SELECT node.id,input.position FROM material_nodes node JOIN selected_material_input input ON input.id=node.id
                UNION ALL
                SELECT child.id,parent.position FROM material_nodes child JOIN selected parent ON child.parent_id=parent.id
            ), deduplicated AS (
                SELECT id,MIN(position) AS position FROM selected GROUP BY id
            )
            SELECT node.id,node.novel_id,node.display_name,node.content,segment.content AS source_content,
                CASE WHEN json_extract(node.content,'$.details.storage')='novel_chapter_cards' THEN (
                    SELECT json_group_array(card.card)
                    FROM novel_chapter_cards card
                    WHERE card.novel_id=node.novel_id
                      AND card.chapter_index BETWEEN
                          CAST(json_extract(node.content,'$.details.chapter_start') AS INTEGER)
                          AND CAST(json_extract(node.content,'$.details.chapter_end') AS INTEGER)
                ) END AS chapter_cards_content
            FROM material_nodes node
            JOIN deduplicated selected ON selected.id=node.id
            LEFT JOIN material_source_segments segment ON segment.material_id=node.id
            ORDER BY selected.position,node.parent_id,node.sort_order,node.display_name
            """,
        ).fetchall()
        novel_ids = selected_novel_ids | {str(row["novel_id"]) for row in selected_nodes}
        novel_lookup = {str(row["id"]): str(row["title"]) for row in connection.execute("SELECT id,title FROM novels")}
        ordered_novel_ids = list(dict.fromkeys([*(str(row["id"]) for row in selected_novels), *(str(row["novel_id"]) for row in selected_nodes)]))
        whole_novel_nodes: dict[str, list[sqlite3.Row]] = {}
        if selected_novel_ids:
            rows = connection.execute(
                "SELECT node.novel_id,node.node_type,node.category,node.display_name,node.content,node.summary,parent.display_name AS dimension_name FROM material_nodes node JOIN selected_material_input selected ON selected.id=node.novel_id LEFT JOIN material_nodes parent ON parent.id=node.parent_id ORDER BY selected.position,parent.sort_order,node.sort_order,node.display_name",
            ).fetchall()
            for row in rows:
                whole_novel_nodes.setdefault(str(row["novel_id"]), []).append(row)
    parts = [f"作品：{novel_lookup[novel_id]}" for novel_id in ordered_novel_ids if novel_id in novel_ids and novel_id in novel_lookup]
    for row in selected_novels:
        parts.append(whole_novel_material_context(str(row["title"]), whole_novel_nodes.get(str(row["id"]), [])))
    for row in {str(row["id"]): row for row in selected_nodes}.values():
        if row["source_content"]:
            content = str(row["source_content"])
        elif row["chapter_cards_content"]:
            try:
                cards = [json.loads(item) for item in json.loads(str(row["chapter_cards_content"]))]
                content = readable_material_content(json.dumps({"chapters": cards}, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                content = readable_material_content(str(row["content"]))
        else:
            content = readable_material_content(str(row["content"]))
        if content:
            parts.append(f"[素材：{row['display_name']}]\n{content}")
    return bounded_material_context(parts)


def store_analysis(path: Path, analysis: dict[str, Any]) -> str:
    novel_id = str(uuid4())
    timestamp = now()
    resolved_path = str(path.resolve())
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM novels WHERE file_path=?", (resolved_path,))
            connection.execute("INSERT INTO novels VALUES (?,?,?,?,?)", (novel_id, path.stem, resolved_path, path.stat().st_size, timestamp))
            for card in analysis.get("chapter_cards", []):
                if not isinstance(card, dict):
                    continue
                connection.execute(
                    "INSERT INTO novel_chapter_cards (id,novel_id,chapter_index,title,start_char,end_char,summary,card,importance,confidence,refined,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        novel_id,
                        int(card["index"]),
                        str(card["title"]),
                        int(card["start_char"]),
                        int(card["end_char"]),
                        str(card["summary"]),
                        json.dumps(card, ensure_ascii=False),
                        int(card.get("importance", 0)),
                        float(card.get("confidence", 0)),
                        int(bool(card.get("refined"))),
                        timestamp,
                    ),
                )
            meta_id = str(uuid4())
            meta = {"primary_type": analysis["primary_type"], "secondary_type": analysis.get("secondary_type", ""), "type_source": analysis.get("type_source", "model"), "coverage": analysis.get("coverage", {})}
            connection.execute("INSERT INTO material_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (meta_id, novel_id, None, "meta", "作品类型", "作品类型", json.dumps(meta, ensure_ascii=False), clean_summary(f"《{path.stem}》的分析类型为{MATERIAL_TYPE_LABELS.get(str(analysis['primary_type']), analysis['primary_type'])}。", "类型来源和后续分类维度已保存，未提供的信息不会被补造。"), json.dumps(analysis_material_tags(analysis), ensure_ascii=False), 0, timestamp))
            dimension_index = 0
            for dimension in analysis.get("dimensions", []):
                items = [item for item in dimension.get("items", []) if isinstance(item, dict)]
                if not items:
                    continue
                dimension_index += 1
                collection_id = str(uuid4())
                name = str(dimension["name"])
                item_descriptions = "；".join(f"{item.get('name') or name}：{item.get('summary') or ''}" for item in items)
                connection.execute("INSERT INTO material_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (collection_id, novel_id, None, "collection", name, name, "{}", clean_summary(f"{name}共提取 {len(items)} 项素材。", item_descriptions), "[]", dimension_index, timestamp))
                for item_index, item in enumerate(items):
                    material_id = str(uuid4())
                    source_content = str(item.get("_source_content") or "")
                    stored_item = {key: value for key, value in item.items() if key != "_source_content"}
                    content = json.dumps(stored_item, ensure_ascii=False)
                    connection.execute("INSERT INTO material_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (material_id, novel_id, collection_id, node_type_for_dimension(name), str(item.get("category") or name), str(item.get("name") or f"{name} {item_index + 1}"), content, clean_summary(item.get("summary"), f"{item.get('name') or name}属于{item.get('category') or name}，完整结构化内容已保存。"), json.dumps(item_material_tags(item), ensure_ascii=False), item_index, timestamp))
                    if source_content:
                        connection.execute("INSERT INTO material_source_segments (material_id,content) VALUES (?,?)", (material_id, source_content))
    return novel_id


def clean_summary(value: Any, fallback: str) -> str:
    summary = re.sub(r"\s+", " ", str(value or "")).strip()
    fallback_text = re.sub(r"\s+", " ", fallback).strip()
    if len(summary) < 50 and fallback_text and fallback_text not in summary:
        summary = f"{summary} {fallback_text}".strip()
    if len(summary) < 50:
        summary = f"{summary} 本摘要只保留原文可确认信息，未提供的背景、关系和规则没有补造。".strip()
    return f"{summary[:197].rstrip()}..." if len(summary) > 200 else summary


def node_type_for_dimension(dimension: str) -> str:
    if "角色" in dimension or dimension in {"写作者", "英雄谱系", "神灵系统"}:
        return "character"
    if any(word in dimension for word in ("世界", "体系", "设定", "规则", "社会", "势力", "背景")):
        return "worldview"
    if any(word in dimension for word in ("情节", "事件", "线索", "伏笔", "爽点", "节奏", "节点", "路径", "时刻", "冲突", "案件", "诡计")):
        return "plot"
    if "主题" in dimension or "寓意" in dimension:
        return "theme"
    return "field"


def list_material_tree() -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        novels = connection.execute("SELECT * FROM novels ORDER BY created_at DESC").fetchall()
        nodes = connection.execute("SELECT * FROM material_nodes ORDER BY novel_id,parent_id,sort_order,display_name").fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in nodes:
        record = dict(row)
        record["tags"] = json.loads(record.get("tags") or "[]")
        grouped.setdefault(str(record["novel_id"]), []).append(record)
    return [{**dict(novel), "nodes": grouped.get(str(novel["id"]), [])} for novel in novels]


def delete_material(material_id: str) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            novel = connection.execute("SELECT id,title FROM novels WHERE id=?", (material_id.strip(),)).fetchone()
            if novel:
                node_ids = [str(row[0]) for row in connection.execute("SELECT id FROM material_nodes WHERE novel_id=?", (material_id,))]
                connection.execute("DELETE FROM novels WHERE id=?", (material_id,))
                return {"id": material_id, "kind": "novel", "name": str(novel["title"]), "deleted_ids": [material_id, *node_ids]}
            node = connection.execute("SELECT id,display_name FROM material_nodes WHERE id=?", (material_id,)).fetchone()
            if not node:
                raise ValueError("素材不存在")
            deleted_ids = [str(row[0]) for row in connection.execute("WITH RECURSIVE descendants(id) AS (SELECT id FROM material_nodes WHERE id=? UNION ALL SELECT child.id FROM material_nodes child JOIN descendants parent ON child.parent_id=parent.id) SELECT id FROM descendants", (material_id,))]
            connection.execute("DELETE FROM material_nodes WHERE id=?", (material_id,))
            return {"id": material_id, "kind": "node", "name": str(node["display_name"]), "deleted_ids": deleted_ids}


def list_pinned_materials(project_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        rows = connection.execute("SELECT p.*,m.display_name,COALESCE(segment.content,m.content) AS content,m.summary,m.tags FROM pinned_materials p JOIN material_nodes m ON m.id=p.material_id LEFT JOIN material_source_segments segment ON segment.material_id=m.id WHERE p.project_id=? ORDER BY p.priority,p.created_at", (project_id,)).fetchall()
    return [dict(row) for row in rows]


def pin_material(project_id: str, material_id: str, priority: int = 0) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        with connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("作品不存在")
            if not connection.execute("SELECT 1 FROM material_nodes WHERE id=?", (material_id,)).fetchone():
                raise ValueError("素材不存在")
            if connection.execute("SELECT COUNT(*) FROM pinned_materials WHERE project_id=?", (project_id,)).fetchone()[0] >= 50 and not connection.execute("SELECT 1 FROM pinned_materials WHERE project_id=? AND material_id=?", (project_id, material_id)).fetchone():
                raise ValueError("常驻素材已达上限(50个)")
            connection.execute("INSERT INTO pinned_materials VALUES (?,?,?,?,?) ON CONFLICT(project_id,material_id) DO UPDATE SET priority=excluded.priority", (str(uuid4()), project_id, material_id, max(0, priority), now()))
    return list_pinned_materials(project_id)


def unpin_material(project_id: str, material_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        with connection:
            connection.execute("DELETE FROM pinned_materials WHERE project_id=? AND material_id=?", (project_id, material_id))
    return list_pinned_materials(project_id)


def pinned_context(project_id: str) -> str:
    parts = []
    for row in list_pinned_materials(project_id)[:50]:
        content = readable_material_content(str(row["content"]))
        if content:
            parts.append(f"[常驻素材：{row['display_name']}]\n{content}")
    return "\n\n".join(parts)


def list_universe_rules(project_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM universe_rules WHERE project_id=? ORDER BY created_at", (project_id,))]


def create_universe_rule(project_id: str, category: str, key: str, value: str, source: str = "manual", immutable: bool = False) -> dict[str, Any]:
    if category not in RULE_CATEGORIES:
        raise ValueError("不支持的铁律分类")
    key, value = key.strip(), value.strip()
    if not key or not value:
        raise ValueError("铁律名称和内容不能为空")
    with closing(connect()) as connection:
        with connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("作品不存在")
            if connection.execute("SELECT COUNT(*) FROM universe_rules WHERE project_id=?", (project_id,)).fetchone()[0] >= 100:
                raise ValueError("宇宙铁律已达上限(100条)")
            rule_id = str(uuid4())
            connection.execute("INSERT INTO universe_rules VALUES (?,?,?,?,?,?,?,?)", (rule_id, project_id, category, key, value, source or "manual", int(immutable), now()))
            row = connection.execute("SELECT * FROM universe_rules WHERE id=?", (rule_id,)).fetchone()
    return dict(row)


def update_universe_rule(rule_id: str, key: str, value: str, immutable: bool | None = None) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM universe_rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                raise ValueError("宇宙铁律不存在")
            if row["immutable"]:
                raise ValueError("不可变铁律不能修改")
            connection.execute("UPDATE universe_rules SET key=?,value=?,immutable=COALESCE(?,immutable) WHERE id=?", (key.strip(), value.strip(), None if immutable is None else int(immutable), rule_id))
            return dict(connection.execute("SELECT * FROM universe_rules WHERE id=?", (rule_id,)).fetchone())


def delete_universe_rule(rule_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT immutable FROM universe_rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                raise ValueError("宇宙铁律不存在")
            if row[0]:
                raise ValueError("不可变铁律不能删除")
            connection.execute("DELETE FROM universe_rules WHERE id=?", (rule_id,))


def import_universe_rules(source_project_id: str, target_project_id: str) -> list[dict[str, Any]]:
    if source_project_id == target_project_id:
        raise ValueError("不能向同一作品重复导入宇宙铁律")
    with closing(connect()) as connection:
        with connection:
            existing_projects = connection.execute(
                "SELECT id FROM projects WHERE id IN (?,?)",
                (source_project_id, target_project_id),
            ).fetchall()
            if len(existing_projects) != 2:
                raise ValueError("源作品或目标作品不存在")
            rules = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM universe_rules WHERE project_id=? ORDER BY created_at",
                    (source_project_id,),
                )
            ]
            current = connection.execute("SELECT COUNT(*) FROM universe_rules WHERE project_id=?", (target_project_id,)).fetchone()[0]
            if current + len(rules) > 100:
                raise ValueError("导入后宇宙铁律超过 100 条")
            for rule in rules:
                connection.execute("INSERT INTO universe_rules VALUES (?,?,?,?,?,?,?,?)", (str(uuid4()), target_project_id, rule["category"], rule["key"], rule["value"], f"project:{source_project_id}", rule["immutable"], now()))
    return list_universe_rules(target_project_id)


def list_facts(project_id: str) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM fact_tables WHERE project_id=? ORDER BY category,key", (project_id,))]


def upsert_fact(project_id: str, category: str, key: str, value: str, source: str = "user") -> dict[str, Any]:
    if category not in FACT_CATEGORIES:
        raise ValueError("不支持的事实分类")
    with closing(connect()) as connection:
        with connection:
            if connection.execute("SELECT COUNT(*) FROM fact_tables WHERE project_id=?", (project_id,)).fetchone()[0] >= 200 and not connection.execute("SELECT 1 FROM fact_tables WHERE project_id=? AND category=? AND key=?", (project_id, category, key)).fetchone():
                raise ValueError("事实表已达上限(200条)")
            fact_id = str(uuid4())
            timestamp = now()
            connection.execute("INSERT INTO fact_tables (id,project_id,category,key,value,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,category,key) DO UPDATE SET value=excluded.value,source=excluded.source,updated_at=excluded.updated_at", (fact_id, project_id, category, key.strip(), value.strip(), source or "user", timestamp, timestamp))
            row = connection.execute("SELECT * FROM fact_tables WHERE project_id=? AND category=? AND key=?", (project_id, category, key.strip())).fetchone()
    return dict(row)


def delete_fact(fact_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            if connection.execute("DELETE FROM fact_tables WHERE id=?", (fact_id,)).rowcount == 0:
                raise ValueError("事实不存在")


def project_memory(project_id: str) -> dict[str, Any]:
    facts = list_facts(project_id)
    grouped: dict[str, dict[str, str]] = {}
    for fact in facts:
        grouped.setdefault(str(fact["category"]), {})[str(fact["key"])] = str(fact["value"])
    return grouped


def list_impacts(project_id: str, unresolved_only: bool = False) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        query = "SELECT * FROM impact_logs WHERE project_id=?"
        args: list[Any] = [project_id]
        if unresolved_only:
            query += " AND resolved=0"
        query += " ORDER BY created_at DESC"
        return [dict(row) for row in connection.execute(query, args)]


def analyze_impact(project_id: str, changed_node_id: str, change_type: str) -> list[dict[str, Any]]:
    if change_type not in {"modify", "delete", "insert"}:
        raise ValueError("不支持的影响类型")
    with closing(connect()) as connection:
        source = connection.execute("SELECT display_name,summary,content FROM material_nodes WHERE id=?", (changed_node_id,)).fetchone()
        if source:
            tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", f"{source['display_name']} {source['summary']}") if token]
        else:
            chapter = connection.execute("SELECT title,content FROM chapters WHERE id=? AND project_id=?", (changed_node_id, project_id)).fetchone()
            tokens = [token for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", f"{chapter['title']} {chapter['content'][:1000]}") if token] if chapter else [changed_node_id]
        candidates = []
        for row in connection.execute("SELECT id,title,content FROM chapters WHERE project_id=?", (project_id,)):
            if str(row[0]) == changed_node_id:
                continue
            text = f"{row[1]} {row[2]}"
            if any(token in text for token in tokens[:12]):
                candidates.append((str(row[0]), text, "causal" if "因为" in text or "所以" in text else "reference", "rewrite" if change_type in {"modify", "delete"} else "review"))
        for row in connection.execute("SELECT id,key,value FROM fact_tables WHERE project_id=?", (project_id,)):
            text = f"{row[1]} {row[2]}"
            if any(token in text for token in tokens[:12]):
                candidates.append((str(row[0]), text, "reference", "review"))
        for row in connection.execute("SELECT id,title,content FROM story_nodes WHERE project_id=?", (project_id,)):
            if str(row[0]) == changed_node_id:
                continue
            text = f"{row[1]} {row[2]}"
            if any(token in text for token in tokens[:12]):
                candidates.append((str(row[0]), text, "foreshadow" if "伏笔" in text else "reference", "rewrite" if change_type in {"modify", "delete"} else "review"))
        logs = []
        with connection:
            connection.execute(
                "UPDATE impact_logs SET resolved=1 WHERE project_id=? AND changed_node_id=? AND resolved=0",
                (project_id, changed_node_id),
            )
            for affected_id, _, relation, action in candidates[:200]:
                log_id = str(uuid4())
                connection.execute("INSERT INTO impact_logs VALUES (?,?,?,?,?,?,?,?,?)", (log_id, project_id, changed_node_id, change_type, affected_id, relation if relation in IMPACT_RELATIONS else "reference", action if action in IMPACT_ACTIONS else "review", 0, now()))
                logs.append(dict(connection.execute("SELECT * FROM impact_logs WHERE id=?", (log_id,)).fetchone()))
    return logs


def resolve_impact(impact_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            if connection.execute("UPDATE impact_logs SET resolved=1 WHERE id=?", (impact_id,)).rowcount == 0:
                raise ValueError("影响记录不存在")


STORY_LAYERS = {"premise", "volume_outline", "chapter_beat", "content", "attachment"}


def story_node_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    try:
        record["metadata"] = json.loads(record.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        record["metadata"] = {}
    record["locked"] = bool(record.get("locked"))
    return record


def list_story_nodes(project_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        if session_id:
            rows = connection.execute(
                "SELECT * FROM story_nodes WHERE project_id=? AND (session_id=? OR session_id IS NULL) ORDER BY layer,parent_id,sort_order,created_at",
                (project_id, session_id),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM story_nodes WHERE project_id=? ORDER BY layer,parent_id,sort_order,created_at", (project_id,)).fetchall()
    return [story_node_record(row) for row in rows]


def create_story_node(
    project_id: str,
    layer: str,
    title: str,
    content: str = "",
    *,
    session_id: str | None = None,
    parent_id: str | None = None,
    node_type: str = "note",
    metadata: dict[str, Any] | None = None,
    locked: bool = False,
) -> dict[str, Any]:
    if layer not in STORY_LAYERS:
        raise ValueError("不支持的结构层级")
    title = title.strip()
    if not title:
        raise ValueError("结构节点标题不能为空")
    node_id = str(uuid4())
    timestamp = now()
    with closing(connect()) as connection:
        with connection:
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("作品不存在")
            if session_id and not connection.execute("SELECT 1 FROM sessions WHERE id=? AND project_id=?", (session_id, project_id)).fetchone():
                raise ValueError("会话不属于当前作品")
            if parent_id and not connection.execute("SELECT 1 FROM story_nodes WHERE id=? AND project_id=?", (parent_id, project_id)).fetchone():
                raise ValueError("父节点不存在")
            sort_order = int(connection.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM story_nodes WHERE project_id=? AND parent_id IS ?", (project_id, parent_id)).fetchone()[0])
            connection.execute(
                "INSERT INTO story_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (node_id, project_id, session_id, parent_id, layer, node_type.strip() or "note", title, content.strip(), json.dumps(metadata or {}, ensure_ascii=False), int(locked), sort_order, timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM story_nodes WHERE id=?", (node_id,)).fetchone()
    return story_node_record(row)


def update_story_node(
    node_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
    locked: bool | None = None,
) -> dict[str, Any]:
    with closing(connect()) as connection:
        with connection:
            row = connection.execute("SELECT * FROM story_nodes WHERE id=?", (node_id,)).fetchone()
            if not row:
                raise ValueError("结构节点不存在")
            values = {
                "title": title.strip() if title is not None else row["title"],
                "content": content.strip() if content is not None else row["content"],
                "metadata": json.dumps(metadata, ensure_ascii=False) if metadata is not None else row["metadata"],
                "locked": int(locked) if locked is not None else row["locked"],
            }
            if not values["title"]:
                raise ValueError("结构节点标题不能为空")
            connection.execute("UPDATE story_nodes SET title=?,content=?,metadata=?,locked=?,updated_at=? WHERE id=?", (values["title"], values["content"], values["metadata"], values["locked"], now(), node_id))
            updated = connection.execute("SELECT * FROM story_nodes WHERE id=?", (node_id,)).fetchone()
    return story_node_record(updated)


def delete_story_node(node_id: str) -> None:
    with closing(connect()) as connection:
        with connection:
            if connection.execute("DELETE FROM story_nodes WHERE id=?", (node_id,)).rowcount == 0:
                raise ValueError("结构节点不存在")


def reorder_story_nodes(project_id: str, parent_id: str | None, node_ids: list[str]) -> list[dict[str, Any]]:
    with closing(connect()) as connection:
        with connection:
            existing = {str(row[0]) for row in connection.execute("SELECT id FROM story_nodes WHERE project_id=? AND parent_id IS ?", (project_id, parent_id))}
            if set(node_ids) != existing or len(node_ids) != len(existing):
                raise ValueError("结构节点顺序不完整")
            for index, node_id in enumerate(node_ids, start=1):
                connection.execute("UPDATE story_nodes SET sort_order=?,updated_at=? WHERE id=?", (index, now(), node_id))
    return list_story_nodes(project_id)


def copy_story_node(node_id: str, target_project_id: str, target_parent_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    with closing(connect()) as connection:
        source = connection.execute("SELECT * FROM story_nodes WHERE id=?", (node_id,)).fetchone()
    if not source:
        raise ValueError("结构节点不存在")
    metadata = json.loads(source["metadata"] or "{}")
    metadata["copied_from"] = node_id
    metadata["source_project_id"] = source["project_id"]
    return create_story_node(
        target_project_id,
        str(source["layer"]),
        str(source["title"]),
        str(source["content"]),
        session_id=session_id,
        parent_id=target_parent_id,
        node_type=str(source["node_type"]),
        metadata=metadata,
        locked=bool(source["locked"]),
    )
