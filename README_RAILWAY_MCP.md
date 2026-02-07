# Railway MCP Setup Guide - IMS 2.0 Backend Fix

## 📋 What This Does

This setup allows Claude Code to directly connect to your Railway deployment so Claude can:
- ✅ Check if services are running
- ✅ View backend logs in real-time
- ✅ Check environment variables
- ✅ Deploy code updates automatically
- ✅ Fix the backend 503 error issue

## 🎯 The Goal

Your IMS 2.0 backend is returning HTTP 503 errors on all API endpoints except login. We need to:
1. Connect Claude to Railway using MCP
2. Have Claude diagnose the root cause
3. Have Claude fix and deploy the solution

---

## 📝 Setup Instructions

### You have 3 files to follow:

**File 1: `RAILWAY_MCP_INSTRUCTIONS.md`**
- Simple step-by-step guide
- Read this first if you're not technical
- Takes 10 minutes to complete

**File 2: `RAILWAY_MCP_SETUP.sh`**
- Automated setup script
- Optional - only use if you're comfortable with terminal
- Does all steps automatically

**File 3: `CLAUDE_RAILWAY_PROMPT.txt`**
- Prompt to paste into Claude Code after setup
- Tells Claude what to diagnose

---

## 🚀 Quick Start (5 Steps)

### 1. **Read the Instructions**
Open `RAILWAY_MCP_INSTRUCTIONS.md` in a text editor and follow Step 1-8

### 2. **Run Setup Commands in Your Terminal**
Copy each command from the instructions and run it on your computer (not in Claude Code)

### 3. **Restart Claude Code**
Close and reopen Claude Code completely

### 4. **Verify Setup**
In Claude Code, type: `@railway-mcp-server check-railway-status`

### 5. **Diagnose the Backend**
Copy the prompt from `CLAUDE_RAILWAY_PROMPT.txt` and paste it into Claude Code

---

## 🛠️ What Each Setup Step Does

| Step | Command | What It Does |
|------|---------|-------------|
| 1 | `npm install -g @railway/cli` | Installs the Railway command-line tool |
| 2 | `railway login --browserless` | Logs you in to Railway |
| 3 | `railway version` | Verifies installation worked |
| 4 | `railway link ...` | Connects to your IMS 2.0 project |
| 5 | `claude mcp add ...` | Adds Railway to Claude Code |
| 6 | Restart Claude Code | Loads the new MCP server |

---

## ✅ Success Indicators

✅ **Setup successful if:**
- You see "Logged in" when running check-railway-status
- You can see your services (ims-2.0-railway + MongoDB)
- You can retrieve deployment logs
- You can see environment variables

❌ **Setup failed if:**
- You get "command not found" errors
- Authentication fails
- Claude Code won't recognize the MCP server

---

## 📞 If You Get Stuck

### Error: "npm: command not found"
- **Fix:** Install Node.js from https://nodejs.org/
- Then run the setup commands again

### Error: "railway: command not found" (after npm install)
- **Fix:** Close and reopen your terminal
- Then try the command again

### Error: "Authentication failed"
- **Fix:** Run `railway login --browserless` again
- Make sure you paste the pairing code correctly

### Claude Code doesn't see the MCP server
- **Fix:** Completely close and reopen Claude Code
- Wait 30 seconds before testing again
- Then type: `@railway-mcp-server check-railway-status`

---

## 🔍 What Claude Will Diagnose

Once connected, Claude will check:

1. **Service Status** → Is the backend running?
2. **Deployment Logs** → Are there error messages?
3. **Environment Variables** → Are all settings correct?
4. **Database Connection** → Can the backend reach MongoDB?
5. **API Health** → Why are endpoints returning 503?

---

## 🎯 Next Steps After Setup

### After you verify the setup works:

1. **Tell Claude to diagnose the issue:**
   ```
   Copy CLAUDE_RAILWAY_PROMPT.txt into Claude Code
   ```

2. **Claude will:**
   - Get the latest logs
   - Identify the root cause
   - Provide a fix
   - Deploy the fix automatically

3. **You'll see:**
   - Backend no longer returns 503 errors
   - `/api/v1/stores` endpoint starts working
   - Login with test accounts works
   - All other API endpoints become available

---

## 📚 Project Information

**Your Project:**
- Name: `brashakg/ims-2.0-railway`
- Backend Service: `ims-2-0-railway` (currently returning 503)
- Database: `MongoDB` (on Railway)
- Frontend: `ims-2-0-railway` (on Vercel)
- Backend URL: `https://ims-20-railway-production.up.railway.app`

**Railway Project ID:** `b9ccf10c-66d9-4632-90a7-98f6f5a23efa`
**Backend Service ID:** `1ddba6c2-e9cb-47df-8bd6-54ddc9761be3`

---

## 🎓 How MCP Works (In Simple Terms)

**MCP = Model Context Protocol**

Think of it like giving Claude a "phone number" to call your Railway deployment.

Before MCP:
- Claude could only look at your code files
- Claude couldn't see if services were running
- Claude couldn't deploy updates

After MCP:
- Claude can call your Railway deployment
- Claude can see logs and status in real-time
- Claude can deploy fixes automatically
- Claude can check what's wrong and fix it

---

## ❓ FAQ

**Q: Do I need to enter my Railway password?**
A: No, you use the pairing code method. It's secure.

**Q: Will this cost money?**
A: No, you're just using tools you already have access to.

**Q: Can Claude see all my code/secrets?**
A: Claude can see logs and environment variable names (not values). You control what it can do.

**Q: What if something goes wrong?**
A: The setup is reversible. You can remove the MCP connection anytime.

---

## 🎉 Ready to Start?

1. Open `RAILWAY_MCP_INSTRUCTIONS.md`
2. Follow steps 1-8
3. Come back to Claude Code
4. Paste the prompt from `CLAUDE_RAILWAY_PROMPT.txt`
5. Let Claude fix your backend! 🚀

---

**Questions?** Ask Claude Code - it will guide you through the setup.
