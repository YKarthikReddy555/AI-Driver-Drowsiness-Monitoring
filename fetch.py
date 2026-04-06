import sqlite3
conn=sqlite3.connect('database.db')
cur=conn.cursor()
cur.execute('SELECT message FROM messages WHERE message_type=\"video\" ORDER BY id DESC LIMIT 5')
for row in cur.fetchall(): print(row[0])
