# Going live — a complete walkthrough

This is the hand-holding version, written for someone who has not deployed to
AWS before. It assumes Windows 11 with WSL2 (Ubuntu) — which is where the deploy
runs, for reasons explained under "Where you type commands" below — and takes
you from nothing to a live, public URL, then back down to zero cost.

`ops/RUNBOOK.md` is the reference — the *why* behind each piece. This is the
*how*, in order, with the exact commands.

---

## Before you start — read this once

**This costs real money.** The backend runs on AWS EKS, which is **not** free
tier. While it is up it costs about **$0.28/hour** (~$7/day, ~$210/month). A
short test session is about **$1–2**. The whole design is built so you bring it
up, use it, and **tear it down the same day**.

**The single most important habit:** when you finish, run `down.sh` and wait for
it to say everything is gone. Forgetting is how you get a surprise bill.

**You will need:**
- A credit/debit card (AWS requires one even for a demo account).
- About **30 minutes** for the first-ever run (AWS takes ~25 min to build the
  cluster), and ~15 minutes to tear down.
- The OpenAI API key you already use for this project.

### ⚠️ Which terminal — read this, it is the thing people get wrong

**Type every command in this guide inside the Ubuntu (WSL2) terminal.** Not Git
Bash, not PowerShell, not CMD. The *only* exceptions are the two or three
commands in Step 1a that are explicitly labelled "in PowerShell", and Docker
Desktop's settings screen. Everywhere else, if a command block does not say
PowerShell, it runs in **Ubuntu**.

**Why Ubuntu and not Git Bash or PowerShell:** Windows blocks the deploy tools
(`helm`, `terraform`) with an **Application Control** policy — they fail with
*"An Application Control policy has blocked this file"*. WSL2 (Ubuntu) is Linux,
that policy does not reach into it, and the deploy scripts are bash scripts that
belong in Linux anyway. Running them in Git Bash or PowerShell gives errors like
`missing required tool: aws`, because the Windows copies of those tools are
blocked or were uninstalled. Docker Desktop already runs on WSL2 under the hood,
so Ubuntu adds no new heavy software.

**How to tell which terminal you are in:**

| You see a prompt like… | You are in… | Use it for the deploy? |
|---|---|---|
| `baghe@DESKTOP:...$` in a window titled *Ubuntu* | **Ubuntu (WSL2)** | ✅ yes, everything |
| `PS C:\Users\...>` | PowerShell | only Step 1a |
| `...MINGW64 ~$` or a `C:/...` path | Git Bash | ❌ no — close it |

If you are not in Ubuntu, open it: click **Start**, type **Ubuntu**, press
Enter.

**If you earlier installed these tools on Windows** (with `winget`) and they got
blocked, you can ignore them — they are not used from here on. Uninstalling them
is optional: `winget uninstall Amazon.AWSCLI Hashicorp.Terraform Helm.Helm
jqlang.jq` in PowerShell.

---

## Step 1 — Set up WSL2 and install the tools

### 1a. Confirm WSL2 is present

In **PowerShell**, run:

```powershell
wsl -l -v
```

If you see a distribution (e.g. `Ubuntu`) with `VERSION 2`, you are ready — open
it from the Start menu by typing "Ubuntu". If the command errors or lists
nothing, install it once with `wsl --install` (from an **Administrator**
PowerShell), then **restart the computer** and open Ubuntu from the Start menu;
it will ask you to create a Linux username and password the first time.

### 1b. Connect Docker Desktop to WSL2

Docker Desktop → **Settings** → **Resources** → **WSL Integration** → enable the
toggle for your Ubuntu distribution → **Apply & Restart**. This lets `docker`
work from inside Ubuntu, which the image build needs.

### 1c. Install the tools (inside Ubuntu — none of these are blocked)

Open **Ubuntu** and paste these blocks one at a time:

```bash
sudo apt update && sudo apt install -y unzip jq
```

```bash
# AWS CLI v2
curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/aws.zip
cd /tmp && unzip -q aws.zip && sudo ./aws/install && cd -
```

```bash
# Terraform
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y terraform
```

```bash
# Helm
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

```bash
# kubectl
sudo curl -sL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl
sudo chmod +x /usr/local/bin/kubectl
```

### 1d. Check everything runs

```bash
aws --version && terraform -version && helm version && kubectl version --client && jq --version && docker --version && node --version
```

Every line should print a version and nothing should say "blocked" or "command
not found". If `docker` fails, re-do step 1b. If `node` is missing, install it
with `sudo apt install -y nodejs npm`.

**Docker Desktop must be running** whenever you deploy — look for the whale icon
in your Windows system tray. The build step needs the engine up.

---

## Step 2 — Create an AWS account and get credentials

### 2a. The account (skip if you already have one)

1. Go to <https://aws.amazon.com/> and click **Create an AWS Account**.
2. Enter an email, account name, and a password.
3. Enter your address and a **credit/debit card** (required; you are not charged
   to create the account).
4. Verify your phone number (they send a code).
5. Choose the **Basic (free)** support plan.

This takes 10–15 minutes and the account is usually usable within a few minutes.

### 2b. Create a login for the CLI

You should not use your root account for day-to-day work. Create an admin user:

1. Sign in to the **AWS Console** at <https://console.aws.amazon.com/>.
2. In the search bar at the top, type **IAM** and open it.
3. Left menu → **Users** → **Create user**.
4. Name it `safeshield-admin`. Click **Next**.
5. Choose **Attach policies directly**, tick **AdministratorAccess**, click
   **Next**, then **Create user**.

### 2c. Get an access key

1. Click the `safeshield-admin` user you just made.
2. Tab **Security credentials** → scroll to **Access keys** → **Create access
   key**.
3. Choose **Command Line Interface (CLI)**, tick the confirmation, **Next**,
   **Create access key**.
4. You now see an **Access key ID** and a **Secret access key**. **Copy both
   now** — the secret is shown only once. (If you lose it, just delete the key
   and make a new one.)

### 2d. Tell WSL about the credentials

In **Ubuntu** (WSL has its own home directory, separate from Windows, so this
must be done here even if you ran it on Windows before):

```bash
aws configure
```

It asks four things:
- **AWS Access Key ID** — paste the key ID from 2c.
- **AWS Secret Access Key** — paste the secret from 2c.
- **Default region name** — type `ap-south-1` (Mumbai; the project defaults to
  it). Use a closer region if you prefer, but then also set it in Step 3.
- **Default output format** — type `json`.

Verify it works:

```bash
aws sts get-caller-identity
```

You should see your account number and the `safeshield-admin` user ARN. If you
see an error, the keys are wrong — re-run `aws configure`.

---

## Step 3 — Get the project onto the Linux side, and create your config

**Do not run the deploy from `/mnt/c` (your Windows Desktop / OneDrive folder).**
`git` and `terraform` write many small files with Unix permissions, and the
Windows drive — especially inside OneDrive — rejects those operations with
errors like *"could not write config file … Permission denied"*. It is also
much slower. The fix is to keep the project in your **WSL home directory**, the
Linux filesystem, exactly as the official WSL docs recommend.

Get a clean copy there by cloning it from GitHub. In **Ubuntu**:

```bash
cd ~
git clone https://github.com/Baghel004/SafeShield.git
cd SafeShield
git checkout phase-1-fastapi-auth
```

> This is a separate copy from the one on your Desktop — that is fine, and
> intended. You only deploy from this Linux copy. Make sure the branch you are
> deploying is pushed to GitHub first (from wherever you normally commit), so
> the clone has your latest code.

Now create the one settings file the deploy reads. It is gitignored, so the
clone does not include it — you make it here:

```bash
cp deploy/terraform/ondemand.tfvars.example deploy/terraform/ondemand.tfvars
nano deploy/terraform/ondemand.tfvars
```

In the editor set two things (save with **Ctrl+O**, Enter, then **Ctrl+X**):

```hcl
deletion_protection = false            # leave this false, or you cannot tear down
billing_alarm_email = "you@example.com"  # <- your real email
```

Leave everything else as-is. **If you chose a region other than `ap-south-1`**
in Step 2d, also add a line `region = "your-region"` here, and set it in Ubuntu
for this session: `export AWS_REGION=your-region`.

---

## Step 4 — Bring it live

Make sure **Docker Desktop is running** (whale icon in the Windows tray). Then,
**in the Ubuntu terminal**, from the project folder:

```bash
cd ~/SafeShield
bash deploy/scripts/up.sh
```

Note the exact path: `deploy/scripts/up.sh` — **`scripts`** with an "s". Running
it with `bash` in front avoids any file-permission surprises.

### What happens, and the one interruption to expect

The script prints its progress. The first phase — building the cluster and
database — takes **~20 minutes**. Let it run.

**On your very first run only, it will stop** with a message like:

```
The application secret is not populated yet. Set it once, then re-run this script:

  aws secretsmanager put-secret-value --region ap-south-1 --secret-id safeshield-prod/app \
    --secret-string '{"JWT_SECRET":"...","OPENAI_API_KEY":"sk-..."}'
```

This is expected and safe. It is telling you to store your secret keys in AWS
(the tool never puts your keys in a file). Do this:

1. Copy the command it printed.
2. Replace `sk-...` with your real OpenAI API key. (The `JWT_SECRET` part
   generates itself — leave that as printed.)
3. Paste and run it **in Ubuntu**. It prints the secret's ARN — that means it
   worked.
4. Run `bash deploy/scripts/up.sh` **again**. It skips the 20 minutes already
   done and continues from where it stopped. This interruption only ever happens
   once; the keys persist.

### When it finishes

You will see:

```
==> Up.
  Frontend: https://xxxxxxxx.cloudfront.net
  API:      https://k8s-xxxx.ap-south-1.elb.amazonaws.com/api/health
```

**Also confirm the billing alarm:** AWS emails the address from Step 3 asking you
to confirm an SNS subscription. Click the link in that email once, so the
"you're spending money" alarm can actually reach you.

---

## Step 5 — Check it works

1. Open the **Frontend** URL in your browser. (Give it 2–3 minutes after the
   script finishes — the load balancer takes a moment to start passing traffic.
   If you see "backend offline", wait and refresh.)
2. Click **Try the demo**.
3. Ask a question, e.g. *"Is cataract treatment excluded from cover?"*
4. You should see an answer stream in with numbered source citations.

That is the whole app, live on the internet. The Frontend URL is the one to put
on a CV or share — it stays up even after you tear the backend down (it will
show the "backend offline" banner when the backend is down, which is fine).

---

## Step 6 — Tear it down (do not skip this)

When you are done for the session, **in Ubuntu**:

```bash
cd ~/SafeShield
bash deploy/scripts/down.sh
```

It removes the release, destroys the infrastructure, and then **checks AWS
directly** that nothing is left running — printing a line for each resource
type. It ends with:

```
==> Down. Nothing hourly is left.
```

If instead it says **"Some resources survived"** and lists items marked
`STILL RUNNING`, those are billing — do not walk away. Re-run `down.sh`; if it
still reports something, open the AWS Console, search for that service, and
delete the item by hand. (This is rare, but it is exactly the situation the
check exists to catch.)

The frontend, the container images, and your saved secret are left in place on
purpose — together they cost about **$1–3/month** and are what keep your public
link alive. To remove those too (only if you are done with the project
entirely), destroy the frontend module:

```bash
terraform -chdir=deploy/terraform-frontend destroy
```

---

## If something goes wrong

- **`missing required tool: aws` (or terraform, helm, jq):** you are almost
  certainly running the script in **Git Bash or PowerShell**, where the Windows
  tools are blocked or gone. Close it, open **Ubuntu**, `cd ~/SafeShield`, and
  run `bash deploy/scripts/up.sh` there. If you get this *inside Ubuntu*, that
  tool is not installed yet — re-do Step 1c.
- **`could not write config file … Permission denied` during `terraform init`:**
  you are running from `/mnt/c` (the Windows drive), which rejects the file
  operations git needs. Clone the project into your Linux home instead and run
  from there — see Step 3.
- **"An Application Control policy has blocked this file" (helm/terraform on
  Windows):** this is the reason the guide uses WSL2. Do not fight it on
  Windows — run the deploy from Ubuntu (Step 1).
- **A script errors with `'\r'` or "bad interpreter":** the file has Windows
  line endings. Fix it once with:
  `sed -i 's/\r$//' deploy/scripts/*.sh` and run again. (This is rare in WSL2.)
- **"missing required tool":** re-do Step 1c for that tool in Ubuntu.
- **"AWS credentials are not configured":** your session expired or keys are
  wrong — re-run `aws configure` (Step 2d). Remember WSL has its own credentials,
  separate from Windows.
- **A pod will not start, or the URL never loads:** the three things a local
  test could not check are IRSA (the app's permission to reach S3), the S3
  bucket, and the load balancer getting a public address. Run
  `kubectl -n safeshield get pods` and
  `kubectl -n safeshield describe pod <name>` and share the output — that is
  where the answer will be.
- **You are unsure whether anything is still running/billing:** run
  `deploy/scripts/down.sh` again. It is safe to run repeatedly and tells you the
  truth about what exists.

---

## The whole thing, condensed

Every line below runs in the **Ubuntu** terminal (not Git Bash, not PowerShell):

```bash
# one-time setup, all inside Ubuntu (WSL2)
#   install aws, terraform, helm, kubectl, jq  (see Step 1c)
aws configure                                                          # keys + ap-south-1 + json
cd ~/SafeShield
cp deploy/terraform/ondemand.tfvars.example deploy/terraform/ondemand.tfvars
#   ...edit that file: billing_alarm_email

# every session (Docker Desktop running)
cd ~/SafeShield
bash deploy/scripts/up.sh    # first time: stops once to set the secret, then re-run
#   ...use the printed Frontend URL...
bash deploy/scripts/down.sh  # ALWAYS, when finished
```
