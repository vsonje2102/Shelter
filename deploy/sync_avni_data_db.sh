
python3 manage.py shell <<ORM
from graphs.sync_Data_db import *
a = avni_sync()
# a.data_update_parallel(2)
a.subject_data_update()

ORM
