# Fixing Non-English Content in Cluster 6 Calls

## Quick Start

### Step 1: Reset the Database
Delete Cluster 6 calls from the database:

```bash
# Option A: Using Python
python << 'EOF'
import os
import psycopg2
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute('DELETE FROM horizon_calls WHERE cluster = %s;', ('6',))
rows_deleted = cur.rowcount
conn.commit()
cur.close()
print(f'✅ Deleted {rows_deleted} calls from Cluster 6')
EOF

# Option B: Using psql (if installed)
psql $DATABASE_URL -c "DELETE FROM horizon_calls WHERE cluster = '6';"
```

### Step 2: Run the Diagnostic API Test
```bash
python diagnose_api.py
```

### Step 3: Re-upload the Work Programme
Upload your Cluster 6 work programme again in the UI

---

## Understanding the Test Results

The diagnostic script tests **three different scenarios**:

### Test 1: API Comparison (WITH vs WITHOUT language filter)

```
WITH language filter ['en']:    3 results
WITHOUT language filter:        3 results
```

**What it means:**
- ✅ **Same count:** API might be ignoring the filter (or both return same content)
- ❌ **Different count:** API IS respecting the filter (good)

---

### Test 2: Using the App's fetch_call_by_topic_id() Function

```
HORIZON-MISS-2024-CARE-01... ✓ ASCII-only (likely English)
HORIZON-CL6-2024-ENERGY-01... ✗ Contains 45 non-ASCII bytes (likely non-English)
```

**What it means:**
- ✅ **ASCII-only:** English content
- ❌ **Non-ASCII characters:** Non-English content detected

---

## Root Cause Analysis

Based on your test results, here's the decision tree:

```
Do the test results show non-English content?
│
├─ YES (Contains non-ASCII characters)
│  │
│  └─ Possible causes:
│     1️⃣  English version doesn't exist in the API
│     2️⃣  API not respecting the language filter parameter
│     3️⃣  Topic is genuinely multi-language (not filterable)
│
└─ NO (Only ASCII/English content)
   │
   └─ Issue is resolved! ✓
```

---

## How to Fix Based on Results

### Scenario 1: API Ignoring the Filter

**Evidence:** With and without filter return same non-English content

**Solution:**
1. This is an **API limitation** on EC's side
2. Contact EC portal support with:
   - Topic ID that failed
   - Output from diagnose_api.py
   - Statement: "Language filter 'languages: [\"en\"]' not working"

**Workaround:**
- Post-process results to detect non-English content
- Filter out or flag results with non-ASCII characters

---

### Scenario 2: English Version Doesn't Exist in API

**Evidence:** API returns 0 results WITH filter, but results WITHOUT filter

**Solution:**
1. The API genuinely doesn't have English translation for this topic
2. Manual actions:
   - Go to https://ec.europa.eu/info/funding-tenders/opportunities/portal/
   - Check if topic has English version there
   - If NO → the API won't have it either (API mirrors the portal)
   - If YES → report API sync issue to EC support

**Workaround:**
- Manually translate or provide description from portal
- Leave blank if English version unavailable

---

### Scenario 3: English Version Exists, API Returns It

**Evidence:** ASCII-only content in test results

**Solution:**
This is working correctly! The issue was likely:
- ✅ Old cached data in database (now deleted)
- ✅ Or specific topics that genuinely don't have English versions

Next time this happens:
1. Delete DB entries for that cluster
2. Re-upload the work programme
3. Fresh data = correct language

---

## Detailed Test Output Examples

### Example 1: API Filter Working
```
Test 1A: WITH language filter (languages: ['en'])
✓ API returned 3 results
  Title: HORIZON-2024-CLIMATE: European climate adaptation strategies
  Description: This call seeks to develop evidence-based adaptation strategies...
  Non-ASCII chars: 0
  Appears English: True
  Detected language: Likely English (ASCII-only)

Test 1B: WITHOUT language filter (no languages parameter)
✓ API returned 5 results
  Title: ГОРИЗОНТ-2024: Европейские климатические стратегии
  Description: Этот конкурс направлен на разработку стратегий адаптации...
  Non-ASCII chars: 87
  Appears English: False
  Detected language: Cyrillic (Bulgarian?)

→ Filter IS WORKING (returned fewer results with filter)
```

**What this means:** The API is correctly filtering for English when you provide `languages: ["en"]`

---

### Example 2: API Ignoring Filter
```
Test 1A: WITH language filter (languages: ['en'])
✓ API returned 3 results
  Title: ГОРИЗОНТ-2024 ЭНЕРГИЯ
  Non-ASCII chars: 45
  Appears English: False

Test 1B: WITHOUT language filter (no languages parameter)
✓ API returned 3 results
  Title: ГОРИЗОНТ-2024 ЭНЕРГИЯ
  Non-ASCII chars: 45
  Appears English: False

→ Filter has NO EFFECT (API ignoring the parameter)
```

**What this means:** The API isn't respecting the language filter - it's returning Cyrillic/Russian regardless

---

## Step-by-Step Recovery Process

### 1. Assess Current State
```bash
# Check current Cluster 6 calls
python << 'EOF'
import os, psycopg2
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM horizon_calls WHERE cluster = %s', ('6',))
count = cur.fetchone()[0]
cur.close()
print(f'Current Cluster 6 calls: {count}')
EOF
```

### 2. Run Diagnostic
```bash
python diagnose_api.py | tee diagnostic_output.txt
```

### 3. Reset Database (if returning old data)
```bash
python << 'EOF'
import os, psycopg2
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute('DELETE FROM horizon_calls WHERE cluster = %s;', ('6',))
deleted = cur.rowcount
conn.commit()
cur.close()
print(f'Deleted {deleted} calls')
EOF
```

### 4. Re-upload Work Programme
- Use the UI to upload the latest Cluster 6 work programme
- Wait for processing to complete

### 5. Verify Results
Check the database again to see the new data:
```bash
python << 'EOF'
import os, psycopg2
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute('''
    SELECT topic_id, title, 
           CASE WHEN title ~ '[^\x00-\x7F]' THEN 'NON-ASCII' ELSE 'ASCII' END as encoding
    FROM horizon_calls 
    WHERE cluster = %s 
    LIMIT 5
''', ('6',))
for row in cur.fetchall():
    print(f'{row[0]}: {row[1][:60]}... [{row[2]}]')
cur.close()
EOF
```

---

## Common Issues & Solutions

### Issue: "DELETE didn't remove enough rows"
- There may be multiple work programmes loaded
- Each topic_id is unique, so deletes by cluster still leaves duplicates
- **Solution:** Use topic_id instead if you exported a list

```bash
# Delete specific topics
python << 'EOF'
import os, psycopg2
topics = ['HORIZON-MISS-2024-CARE-01', 'HORIZON-MISS-2024-CARE-02']
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
for topic in topics:
    cur.execute('DELETE FROM horizon_calls WHERE topic_id = %s;', (topic,))
deleted = cur.rowcount
conn.commit()
cur.close()
print(f'Deleted {deleted} calls')
EOF
```

### Issue: "API still returns non-English after deletion"
- Fresh API calls are returning non-English content
- This means the **API itself** has the non-English version
- **Next step:** Check if English version exists in the portal

### Issue: "API returns 0 results after adding filter"
- This could mean:
  1. Topic doesn't exist in API at all
  2. Topic only has non-English versions (no English in API)
- **Solution:** Verify topic exists in portal UI first

---

## Questions About Test Results

**Q: What are "non-ASCII characters"?**
A: Characters outside the basic English ASCII range (0-127). These include accents, Cyrillic, Greek, etc.

**Q: What if some topics are ASCII and others aren't?**
A: Some topics might genuinely not have English versions in the API. This is expected for newer or less-translated topics.

**Q: Can I fix non-English content programmatically?**
A: Not reliably. You'd need to either:
1. Translate them (using Google Translate API, etc.)
2. Flag them for manual review
3. Exclude them from the app

---

## Next Actions

After running the diagnostic:

1. **Send me the output** of `python diagnose_api.py`
2. **Tell me:** Are the test results showing English or non-English content?
3. **We'll determine** if it's:
   - A database cache issue (solved by deletion + re-upload)
   - An API filter issue (needs EC support ticket)
   - A missing English version issue (API limitation)

---

## Database Backup (Safety First)

Before making changes, back up your database:

```bash
# PostgreSQL backup
pg_dump $DATABASE_URL > horizon_backup_before_cluster6_reset.sql

# Then delete
python << 'EOF'
import os, psycopg2
db_url = os.environ.get('DATABASE_URL')
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute('DELETE FROM horizon_calls WHERE cluster = %s;', ('6',))
conn.commit()
cur.close()
EOF

# If you need to restore
psql $DATABASE_URL < horizon_backup_before_cluster6_reset.sql
```

---

This guide will help you isolate whether the issue is:
- **Database cache** → Fixed by deletion + re-upload
- **API filter not working** → Needs EC support
- **English version not available** → Needs manual intervention

Ready to run the diagnostic? Execute `python diagnose_api.py` and share the results!
