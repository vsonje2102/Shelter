
#!/bin/bash
python3 manage.py shell << ORM
import graphs.sync_Data_db as sync_module
from importlib import reload
reload(sync_module)
a = sync_module.avni_sync()
print(a.get_cognito_token())
a.data_update_parallel(2)
# a.subject_data_update('Sheet1')
ORM