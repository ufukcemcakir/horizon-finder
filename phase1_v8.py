from modular.phase1_db import init_db, migrate_db
from modular.phase1_ui import main


init_db()
migrate_db()
main()