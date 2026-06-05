@echo off
cd /d C:\Users\Lordy\jobhuntrr
echo Repairing corrupted database...
if exist data\jobs.db.bak del data\jobs.db.bak
if exist data\jobs.db move data\jobs.db data\jobs.db.bak
python -c "from storage.job_store import JobStore; s=JobStore(); print('DB recreated:', s.stats())"
echo Done. You can delete data\jobs.db.bak when satisfied.
pause
