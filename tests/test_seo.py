
import pytest
from django.urls import reverse

@pytest.mark.django_db
class TestSEO:
    """Tests for SEO optimization features."""
    
    @pytest.fixture(autouse=True)
    def setup_settings(self, settings):
        """Configure settings for all tests in this class."""
        settings.STATUS_PAGE_URL = "https://status.sefaria.org"
        # Use simple storage to avoid Missing staticfiles manifest entry error
        settings.STORAGES = {
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }

    def test_robots_txt(self, client):
        """Test robots.txt is served correctly."""
        url = reverse("monitoring:robots_txt")
        response = client.get(url)
        
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain"
        content = response.content.decode("utf-8")
        assert "User-agent: *" in content
        assert "Allow: /" in content
        assert "Sitemap: https://status.sefaria.org/sitemap.xml" in content

    def test_sitemap_xml(self, client):
        """Test sitemap.xml is served correctly."""
        url = reverse("monitoring:sitemap_xml")
        response = client.get(url)
        
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        content = response.content.decode("utf-8")
        assert '<?xml version="1.0" encoding="UTF-8"?>' in content
        assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in content
        assert '<loc>https://status.sefaria.org/</loc>' in content

    def test_meta_tags(self, client):
        """Test homepage contains expected meta tags."""
        url = reverse("monitoring:status")
        response = client.get(url)
        
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        
        # Check Title
        assert "<title>Is Sefaria Down? - Sefaria Status & Service Health</title>" in content
        
        # Check Description (accounting for multi-line formatting)
        assert 'name="description"' in content
        assert 'Check if Sefaria is down' in content
        
        # Check Keywords
        assert 'name="keywords"' in content
        assert 'is sefaria down' in content
        
        # Check Canonical
        assert 'rel="canonical"' in content
        assert 'https://status.sefaria.org/' in content
        
        # Check Open Graph
        assert 'og:title' in content
        assert 'Is Sefaria Down?' in content
        assert 'og:type' in content
        assert 'website' in content
        
        # Check JSON-LD
        assert 'application/ld+json' in content
        assert '"@type": "WebSite"' in content
        assert '"name": "Sefaria Status"' in content
