# IMS 2.0 - Training & Onboarding Guide

## New Team Member Training Program

**Duration**: 2 weeks
**Audience**: Developers, DevOps, Operations, QA
**Last Updated**: February 8, 2026

---

## Week 1: Foundation & Setup

### Day 1: Welcome & Environment Setup (4 hours)

**Morning (2 hours)**:
1. Welcome presentation
   - Company mission & values
   - IMS 2.0 product overview
   - Team structure

2. Account setup
   - GitHub access
   - AWS account
   - Vercel project access
   - Slack workspace
   - Documentation access

**Afternoon (2 hours)**:
1. Development environment setup
   ```bash
   # Clone repository
   git clone https://github.com/brashakg/ims-2.0-railway.git

   # Setup dependencies
   cd frontend && npm install
   cd ../backend && pip install -r requirements.txt

   # Start local development
   docker-compose up -d
   ```

2. Verify setup works
   - Frontend: http://localhost:5173
   - Backend: http://localhost:8000/docs
   - Database: localhost:5432

**Homework**: Read API_DOCUMENTATION.md (30 minutes)

---

### Day 2: Architecture & Technology Stack (6 hours)

**Morning (3 hours)**:
1. System architecture deep dive
   - Read ARCHITECTURE_GUIDE.md
   - Ask questions about components
   - Understand data flow

2. Technology choices
   - Why PostgreSQL + Redis
   - Why FastAPI + React
   - Trade-offs discussed

**Afternoon (3 hours)**:
1. Live demo
   - Start a local instance
   - Login with test account
   - Browse products, create order
   - Trace request through system

2. Code walkthrough
   - Frontend: React component structure
   - Backend: API endpoint example
   - Database: Schema explanation

**Homework**: Setup local environment, verify everything working

---

### Day 3: Development Workflow (6 hours)

**Morning (3 hours)**:
1. Git workflow
   ```bash
   # Create feature branch
   git checkout -b feature/my-feature

   # Make changes
   # Commit frequently
   git commit -m "Feature: description"

   # Push and create PR
   git push origin feature/my-feature
   # Create PR on GitHub
   ```

2. Code review process
   - How to request review
   - How to review code
   - Common feedback patterns

**Afternoon (3 hours)**:
1. Testing overview
   ```bash
   # Frontend tests
   cd frontend && npm test

   # Backend tests
   cd backend && pytest tests/
   ```

2. Running linters
   ```bash
   # Frontend
   npm run lint

   # Backend
   black api/ && pylint api/
   ```

**Homework**: Complete first code review exercise

---

### Day 4: Database & APIs (6 hours)

**Morning (3 hours)**:
1. Database schema exploration
   ```sql
   -- Connect to local database
   psql -h localhost -U postgres -d ims_db

   -- List tables
   \dt

   -- View schema
   \d customers
   ```

2. Common queries
   - SELECT with WHERE/JOIN
   - Indexes and performance
   - Transactions & ACID

**Afternoon (3 hours)**:
1. API testing with Postman
   - Import API collection
   - Test authentication
   - Test CRUD operations

2. Using Swagger UI
   - Navigate to http://localhost:8000/docs
   - Test endpoints interactively
   - Understand request/response

**Homework**: Create 5 database queries for common use cases

---

### Day 5: Security & Compliance (6 hours)

**Morning (3 hours)**:
1. Security overview
   - Read SECURITY_HARDENING.md
   - Understand 2FA setup
   - Review RBAC system

2. Compliance requirements
   - GDPR principles
   - SOX requirements
   - Data handling policies

**Afternoon (3 hours)**:
1. Hands-on security
   - Enable 2FA for account
   - Test authentication flows
   - Review audit logs

2. Secret management
   - How to handle passwords
   - Environment variable setup
   - AWS Secrets Manager intro

**Homework**: Complete security training quiz

---

## Week 2: Specialization Tracks

### Backend Developer Track

**Day 1: FastAPI Deep Dive** (6 hours)

1. FastAPI features (3 hours)
   - Route decorators
   - Request/response models
   - Dependency injection
   - Middleware

2. Building an endpoint (3 hours)
   - Create new route
   - Add validation
   - Add error handling
   - Add logging

**Assignment**: Create a new API endpoint for a feature

---

**Day 2: Database & ORM** (6 hours)

1. SQLAlchemy ORM (3 hours)
   - Session management
   - Query building
   - Relationships
   - Performance optimization

2. Migrations with Alembic (3 hours)
   - Create migration
   - Apply migration
   - Rollback migration
   - Version management

**Assignment**: Create database schema for new feature

---

**Day 3: Testing & Debugging** (6 hours)

1. Unit testing (3 hours)
   - Test fixtures
   - Mocking
   - Assertions
   - Coverage targets

2. Debugging (3 hours)
   - Using PyCharm debugger
   - Logging
   - Error analysis
   - Performance profiling

**Assignment**: Write tests for existing endpoint

---

### Frontend Developer Track

**Day 1: React & Component Architecture** (6 hours)

1. React fundamentals (3 hours)
   - JSX syntax
   - Components (functional)
   - Props & state
   - Hooks (useState, useEffect, useContext)

2. TypeScript in React (3 hours)
   - Typing components
   - Props interfaces
   - Event handlers
   - Custom hooks

**Assignment**: Create a new React component

---

**Day 2: State Management & API Integration** (6 hours)

1. React Query (3 hours)
   - useQuery for fetching
   - useMutation for updates
   - Caching strategy
   - Error handling

2. API integration (3 hours)
   - Making API calls
   - Authentication headers
   - Error handling
   - Loading states

**Assignment**: Integrate API endpoint in UI component

---

**Day 3: Styling & Accessibility** (6 hours)

1. TailwindCSS (3 hours)
   - Utility classes
   - Responsive design
   - Dark mode
   - Custom components

2. Accessibility (3 hours)
   - ARIA attributes
   - Keyboard navigation
   - Screen reader testing
   - Color contrast

**Assignment**: Build accessible form component

---

### DevOps/Operations Track

**Day 1: Infrastructure & Deployment** (6 hours)

1. Infrastructure overview (3 hours)
   - AWS services
   - Docker & containers
   - Kubernetes basics
   - Terraform IaC

2. Deployment process (3 hours)
   - CI/CD pipeline
   - GitHub Actions
   - Vercel deployment
   - ECS deployment

**Assignment**: Deploy code change to staging

---

**Day 2: Monitoring & Alerting** (6 hours)

1. Observability (3 hours)
   - CloudWatch logs
   - Prometheus metrics
   - Grafana dashboards
   - Alert configuration

2. Troubleshooting (3 hours)
   - Reading logs
   - Analyzing metrics
   - Root cause analysis
   - Incident response

**Assignment**: Investigate and fix a simulated issue

---

**Day 3: Backup & Disaster Recovery** (6 hours)

1. Backup strategies (3 hours)
   - Database backups
   - Application backups
   - Recovery procedures
   - Testing restores

2. Disaster scenarios (3 hours)
   - Database failure
   - Service outage
   - Data corruption
   - Recovery procedures

**Assignment**: Execute a disaster recovery drill

---

## Continuous Learning

### Daily Standup (15 minutes)

Every morning:
1. What did you do yesterday?
2. What will you do today?
3. Any blockers?

### Weekly Tech Talks (1 hour)

Rotation topics:
- New feature deep dive
- Security topic
- Performance optimization
- Architecture decision
- Tool or library overview

### Monthly Retrospectives (1 hour)

Review:
- What went well?
- What could improve?
- Process changes?
- Team feedback?

---

## Knowledge Resources

### Essential Reading

1. **API Documentation** (1-2 hours)
   - Path: `/root/ims-2.0-railway/API_DOCUMENTATION.md`
   - Review all endpoints relevant to role

2. **Architecture Guide** (1-2 hours)
   - Path: `/root/ims-2.0-railway/ARCHITECTURE_GUIDE.md`
   - Understand system design

3. **Security Hardening** (1 hour)
   - Path: `/root/ims-2.0-railway/SECURITY_HARDENING.md`
   - Know security practices

4. **Operations Runbook** (1 hour)
   - Path: `/root/ims-2.0-railway/OPERATIONS_RUNBOOK.md`
   - Familiar with incident response

### Video Tutorials

- FastAPI course (YouTube, 4 hours)
- React 18 fundamentals (YouTube, 3 hours)
- Docker & containers (YouTube, 2 hours)
- Kubernetes basics (YouTube, 2 hours)

### Interactive Learning

- Local development setup
- Writing your first API endpoint
- Building your first React component
- Deploying a change
- Investigating a log

---

## Skill Assessment

### Week 1 Assessment

By end of week 1, should understand:
- ✅ System architecture
- ✅ Technology stack
- ✅ Development workflow
- ✅ How to read code
- ✅ Basic security practices

### Week 2 Assessment

By end of week 2, role-specific:

**Backend Developers**:
- ✅ Create API endpoint
- ✅ Write database query
- ✅ Add/update test
- ✅ Deploy code change

**Frontend Developers**:
- ✅ Build React component
- ✅ Integrate API call
- ✅ Add error handling
- ✅ Test component

**DevOps Engineers**:
- ✅ Deploy application
- ✅ Monitor system
- ✅ Investigate issue
- ✅ Execute recovery

---

## Mentoring & Support

### Pair Programming

- **When**: First week, 2-3 sessions
- **Duration**: 1-2 hours each
- **Purpose**: Hands-on learning, code review
- **Mentor**: Assigned senior engineer

### Office Hours

- **When**: Every Tuesday & Thursday, 3-4 PM UTC
- **Who**: Engineering leads available
- **Purpose**: Answer questions, unblock
- **Slack**: #training-support

### Resources

| Role | Mentor | Email | Availability |
|------|--------|-------|--------------|
| **Backend** | John | john@ims-2.0.com | Anytime |
| **Frontend** | Jane | jane@ims-2.0.com | After 3 PM |
| **DevOps** | Bob | bob@ims-2.0.com | Weekday mornings |

---

## Ongoing Development

### After First Month

- Take on small features
- Lead code reviews
- Contribute to documentation
- Help onboard next team member

### After 3 Months

- Own complete features
- Mentor junior developers
- Improve internal processes
- Speak at tech talks

### After 6 Months

- Lead project initiatives
- Mentor others
- Drive technical improvements
- Shape company culture

---

## Feedback & Success

**Success looks like**:
- ✅ Comfortable with codebase
- ✅ Productive on first feature
- ✅ Understands system design
- ✅ Writes quality code
- ✅ Participates in team discussions
- ✅ Helps others learn

**Feedback gathering**:
- 1-on-1s with mentor (weekly)
- Manager check-ins (bi-weekly)
- 30-day review
- 90-day review

---

## Questions?

Post in **#training-support** Slack channel or reach out to your mentor!

**Welcome to the team! 🎉**
