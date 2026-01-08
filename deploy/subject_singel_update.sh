
python3 manage.py shell <<ORM
from graphs.sync_Data_db import *
a = avni_sync()
a.update_household_details("")

ORM