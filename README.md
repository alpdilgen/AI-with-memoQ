# Translation Management System

Professional translation management platform with memoQ Server integration, AI-powered translation, and comprehensive TM/TB management.

## Features

- **AI-Powered Translation**: OpenAI GPT-4 integration for high-quality translations
- **memoQ Server Integration**: Direct connection to memoQ Server for TM/TB access
- **Translation Memory (TM)**: Support for TMX files and memoQ Server TMs
- **Termbase (TB)**: CSV termbase support and memoQ Server TBs
- **Smart Prompt Builder**: Generate optimized prompts from analysis reports and style guides
- **Semantic Reference Matching**: OpenAI embedding-based style reference
- **Batch Processing**: Efficient processing with configurable batch sizes
- **Project Management**: Organize translations into projects
- **Modern UI**: Clean, responsive React interface

## Tech Stack

### Backend
- **FastAPI**: High-performance Python web framework
- **Supabase**: PostgreSQL database with built-in auth
- **Python 3.11**: Core translation services

### Frontend
- **React 18**: Modern UI framework
- **Vite**: Fast build tool
- **TailwindCSS**: Utility-first CSS
- **React Router**: Client-side routing

### Infrastructure
- **Docker**: Containerized deployment
- **Supabase**: Database and authentication

## Getting Started

### Prerequisites

- Node.js 18+
- Python 3.11+
- Docker (optional, for containerized deployment)
- Supabase account

### Environment Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

3. Configure your Supabase credentials:
   - Create a new project at [supabase.com](https://supabase.com)
   - Copy the project URL and anon key
   - Update `.env` with your credentials

### Database Setup

The database schema is automatically created via Supabase migrations. Ensure you have:

- Enabled Row Level Security (RLS) on all tables
- Configured authentication policies
- Set up proper indexes

### Local Development

#### Option 1: Docker Compose (Recommended)

```bash
# Build and start all services
docker-compose up --build

# Access the application
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

#### Option 2: Manual Setup

**Backend:**

```bash
# Install Python dependencies
pip install -r requirements.txt
pip install -r backend/requirements.txt

# Run the backend server
cd backend
uvicorn main:app --reload --port 8000
```

**Frontend:**

```bash
# Install Node dependencies
cd frontend
npm install

# Run the development server
npm run dev
```

Access the application at `http://localhost:3000`

## Deployment

### Subdomain Deployment

This application is designed to be deployed as a subdomain (e.g., `translate.yourdomain.com`).

#### Using Docker

1. Build the containers:

```bash
docker-compose build
```

2. Deploy to your server:

```bash
# Copy docker-compose.yml and .env to your server
# Start services
docker-compose up -d
```

3. Configure your reverse proxy (Nginx/Apache):

**Nginx Example:**

```nginx
server {
    listen 80;
    server_name translate.yourdomain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

4. Set up SSL with Let's Encrypt:

```bash
certbot --nginx -d translate.yourdomain.com
```

### Environment Variables

Required environment variables:

- `VITE_SUPABASE_URL`: Your Supabase project URL
- `VITE_SUPABASE_ANON_KEY`: Your Supabase anonymous key
- `VITE_API_URL`: Backend API URL (default: http://localhost:8000/api)

## Usage

### 1. Create a Project

- Log in to the application
- Click "New Project"
- Enter project name and select languages

### 2. Upload XLIFF File

- Open your project
- Drag and drop or click to upload an XLIFF file
- System will parse segments automatically

### 3. Configure Resources

**memoQ Server Connection:**
- Go to Settings
- Add memoQ Server connection
- Enter server URL, username, and password
- Select TMs and TBs to use

**Local Resources:**
- Upload TMX files for Translation Memory
- Upload CSV files for Termbase
- Upload reference files for style matching

### 4. Start Translation

- Configure API key (OpenAI)
- Adjust settings (batch size, thresholds)
- Click "Start Translation"
- Monitor progress in real-time

### 5. Download Results

- Once completed, download translated XLIFF
- Review translation logs
- Download detailed reports

## API Documentation

Full API documentation is available at `/docs` when running the backend server.

### Key Endpoints

- `POST /api/projects` - Create new project
- `POST /api/projects/{id}/upload` - Upload XLIFF file
- `POST /api/projects/{id}/translate` - Start translation
- `GET /api/projects/{id}/download` - Download results
- `POST /api/memoq/connect` - Connect to memoQ Server
- `GET /api/memoq/{id}/tms` - List memoQ TMs
- `GET /api/memoq/{id}/tbs` - List memoQ TBs

## Architecture

```
├── frontend/              # React frontend application
│   ├── src/
│   │   ├── components/   # Reusable UI components
│   │   ├── pages/        # Page components
│   │   ├── lib/          # Utilities and API client
│   │   └── App.jsx       # Main application component
│   └── package.json
│
├── backend/              # FastAPI backend
│   ├── main.py          # API endpoints
│   └── requirements.txt
│
├── services/            # Core translation services
│   ├── ai_translator.py
│   ├── tm_matcher.py
│   ├── tb_matcher.py
│   ├── memoq_server_client.py
│   └── prompt_builder.py
│
├── utils/               # Utility functions
│   ├── xml_parser.py
│   └── logger.py
│
└── docker-compose.yml   # Container orchestration
```

## Security

- All API endpoints require authentication
- Row Level Security (RLS) enabled on all database tables
- Passwords encrypted in transit and at rest
- API keys never stored in database
- CORS configured for production domains

## Performance

- Batch processing for efficient API usage
- Caching for TM/TB data
- Optimized database queries with indexes
- CDN-ready static assets

## Support

For issues, questions, or contributions, please refer to the project repository.

## License

All rights reserved.
