import sqlite3
conn=sqlite3.connect('database.db')
cur=conn.cursor()
cur.execute('SELECT message FROM messages WHERE message_type=\"image\" ORDER BY id DESC LIMIT 1')
print(cur.fetchone()[0])
