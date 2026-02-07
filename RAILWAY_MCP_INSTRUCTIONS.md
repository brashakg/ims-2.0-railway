# 🚀 Railway MCP Setup - Simple Instructions

**Goal:** Connect Claude Code to your Railway deployment so Claude can monitor and fix your backend.

---

## ⚠️ IMPORTANT: Run on YOUR Computer (Not in Claude Code)

These commands must be run in your **local terminal** on your computer, NOT in Claude Code.

If you're on Windows, use PowerShell or Command Prompt.
If you're on Mac/Linux, use Terminal.

---

## Step-by-Step Setup

### **Step 1: Open Terminal on Your Computer**

**Windows:** Open PowerShell or Command Prompt
**Mac/Linux:** Open Terminal

### **Step 2: Navigate to Your Project Folder**

```bash
cd /path/to/your/ims-2.0-railway/backend
```

Or just navigate to the folder where you downloaded the project using File Explorer, then right-click → "Open Terminal Here" (on Windows) or "New Terminal at Folder" (on Mac).

### **Step 3: Install Railway CLI**

Copy and paste this command:

```bash
npm install -g @railway/cli
```

Wait for it to finish. It will say "added X packages" when done.

### **Step 4: Login to Railway**

Copy and paste this command:

```bash
railway login --browserless
```

**What happens next:**
- Railway will give you a **pairing code** (looks like: `ABC123XYZ`)
- Copy that code
- Open this link in your browser: https://railway.app
- Paste the code and click "Approve"
- Come back to Terminal

### **Step 5: Verify It Works**

Copy and paste this command:

```bash
railway version
```

If it shows a version number, you're good! ✅

### **Step 6: Link Claude Code to Railway**

Copy and paste this command:

```bash
railway link --project b9ccf10c-66d9-4632-90a7-98f6f5a23efa --environment production --service 1ddba6c2-e9cb-47df-8bd6-54ddc9761be3
```

Press Enter. It will confirm the link was created.

### **Step 7: Add Railway to Claude Code**

Copy and paste this command:

```bash
claude mcp add railway-mcp-server -- npx -y @railway/mcp-server
```

Wait for it to finish. It will say "added MCP server" when done.

### **Step 8: Restart Claude Code**

Close and reopen Claude Code completely.

---

## ✅ Verify Everything Worked

In Claude Code, copy and paste this message:

```
@railway-mcp-server check-railway-status
```

**If you see "Logged in" → Everything works! ✅**

If you see an error → Run the setup commands again.

---

## 📋 What You Should See

After setup, you should be able to ask Claude Code:

- "What is the status of my Railway services?"
- "Show me the recent deployment logs"
- "Check the environment variables"
- "Is the backend API running?"
- "Deploy the latest code to Railway"

---

## 🆘 Stuck?

If you get an error during setup:

1. **"command not found"** → You need to install Node.js from nodejs.org
2. **"Authentication failed"** → Make sure you pasted the pairing code correctly
3. **"Project not found"** → Make sure you're in the correct directory

---

## 📞 Next Steps

Once setup is complete:

1. Ask Claude Code: **"Get the latest logs from the ims-2.0-railway backend service"**
2. Share the logs with Claude
3. Claude will diagnose why the backend is returning 503 errors
4. Claude will fix it and deploy the fix automatically

---

**That's it! You're done with setup.** 🎉
