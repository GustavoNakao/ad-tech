start "S-Produto - Servidor" cmd /k python manage.py runserver
 
timeout /t 10 /nobreak
 
start http://127.0.0.1:8000/gerador-kits/
 
