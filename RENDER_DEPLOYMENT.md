# Render.com Deployment Guide

## Step 1: Create PostgreSQL Database

1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **"New +"** → **"PostgreSQL"**
3. Configure:
   - **Name**: `network-slicer-db`
   - **Database**: `network_slicer_db`
   - **User**: `network_slicer_user`
   - **Region**: Oregon (or same as your web service)
   - **Plan**: **Free**
4. Click **"Create Database"**
5. Wait for provisioning (~2-3 minutes)

## Step 2: Get Database Credentials

Once the database is created:

1. Go to your PostgreSQL database page
2. Click on the **"Info"** tab
3. You'll see:
   ```
   Hostname: dpg-xxxxx-a.oregon-postgres.render.com
   Port: 5432
   Database: network_slicer_db
   Username: network_slicer_user
   Password: [click to reveal]
   ```
4. **Copy these values** - you'll need them in the next step

## Step 3: Configure Environment Variables in Web Service

1. Go to your **network-slicer** web service
2. Click **"Environment"** in the left sidebar
3. Add these environment variables (click "Add Environment Variable" for each):

```bash
# Required
DB_HOST=dpg-xxxxx-a.oregon-postgres.render.com
DB_NAME=network_slicer_db
DB_USER=network_slicer_user
DB_PASSWORD=your-actual-password-here
DB_PORT=5432

# Django Settings
SECRET_KEY=your-generated-secret-key
DEBUG=False
ENABLE_WIFI_SLICING=False
```

### Generate SECRET_KEY:

Run this locally:
```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

## Step 4: Deploy

1. Click **"Manual Deploy"** → **"Deploy latest commit"**
2. Watch the logs for:
   ```
   ==> Installing psycopg2-binary
   ==> Running migrations
   ==> Superuser created: username=admin, password=admin123
   ```

## Step 5: Access Your App

- **URL**: https://network-slicer.onrender.com
- **Admin Panel**: https://network-slicer.onrender.com/admin/
- **Login**:
  - Username: `admin`
  - Password: `admin123`

⚠️ **Change the admin password immediately after first login!**

## Troubleshooting

### Issue: "no such table: auth_user"
**Solution**: Database not connected. Verify all DB_* environment variables are set correctly.

### Issue: "Scheme '://' is unknown"
**Solution**: DATABASE_URL is malformed. Use individual DB_* variables instead.

### Issue: Still using SQLite
**Solution**: Make sure `DB_HOST` environment variable is set. The app uses PostgreSQL only if DB_HOST exists.

### Check Which Database is Being Used

Add this to your web service environment temporarily:
```bash
DJANGO_SETTINGS_MODULE=network_slicer.settings
```

Then check the logs during startup - it will show the database configuration.

## Local Development with .env File

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your local or Render database credentials

3. Install python-decouple:
   ```bash
   pip install python-decouple
   ```

4. Run migrations:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```

## Support

If you encounter issues:
1. Check Render logs: Service → Logs tab
2. Verify all environment variables are set
3. Ensure PostgreSQL database is running (green status)
4. Test database connection from Render Shell (if available on paid plan)
