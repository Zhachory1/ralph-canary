\getenv app_password FLAG_DB_PASSWORD
CREATE ROLE flag_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE flags TO flag_app;
