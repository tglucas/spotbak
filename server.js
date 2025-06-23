const express = require('express');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;

// Set view engine
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// Serve static files
app.use(express.static(path.join(__dirname, 'public')));

// Dynamically discover and load all spotbak JSON files
const spotbakData = {};
const datasetInfo = {};

function loadSpotbakFiles() {
    try {
        const files = fs.readdirSync('./').filter(file => 
            file.startsWith('spotbak') && file.endsWith('.json')
        );
        
        console.log(`🔍 Found ${files.length} spotbak JSON files:`);
        
        files.forEach(filename => {
            try {
                const data = JSON.parse(fs.readFileSync(`./${filename}`, 'utf8'));
                const datasetName = filename.replace('spotbak_', '').replace('.json', '');
                
                spotbakData[datasetName] = data;
                
                // Analyze the data structure to determine type and key fields
                const sampleItem = Array.isArray(data) ? data[0] : data;
                const analysis = analyzeDataStructure(sampleItem, datasetName);
                
                datasetInfo[datasetName] = {
                    filename: filename,
                    count: Array.isArray(data) ? data.length : 1,
                    type: analysis.type,
                    displayName: analysis.displayName,
                    fields: analysis.fields,
                    hasImages: analysis.hasImages,
                    searchFields: analysis.searchFields
                };
                
                console.log(`  ✅ ${filename} - ${datasetInfo[datasetName].count.toLocaleString()} ${analysis.displayName}`);
            } catch (error) {
                console.error(`  ❌ Error loading ${filename}:`, error.message);
            }
        });
        
        console.log(`📊 Total datasets loaded: ${Object.keys(spotbakData).length}`);
    } catch (error) {
        console.error('Error discovering spotbak files:', error.message);
    }
}

function analyzeDataStructure(item, datasetName) {
    if (!item) return { type: 'unknown', displayName: datasetName, fields: [], hasImages: false, searchFields: [] };
    
    // Extract the actual data object (handle nested structures)
    let dataObj = item.spotify_json || item;
    
    // Handle special cases for nested structures
    if (datasetName.includes('album') && item.album) {
        dataObj = item.album; // Albums are nested under 'album' key
    } else if (datasetName.includes('show') && item.show) {
        dataObj = item.show; // Shows are nested under 'show' key
    } else if (datasetName.includes('track') && item.track) {
        dataObj = item.track; // Some tracks are nested under 'track' key
    }
    
    let type = 'generic';
    let displayName = datasetName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    let hasImages = false;
    let searchFields = [];
    
    // Determine data type based on structure and naming
    if (datasetName.includes('artist') || dataObj.type === 'artist') {
        type = 'artists';
        displayName = 'Artists';
        searchFields = ['name', 'genres'];
    } else if (datasetName.includes('track') || dataObj.type === 'track' || dataObj.track) {
        type = 'tracks';
        displayName = 'Tracks';
        searchFields = ['name', 'artists', 'album'];
    } else if (datasetName.includes('playlist') || dataObj.type === 'playlist') {
        type = 'playlists';
        displayName = 'Playlists';
        searchFields = ['name', 'description', 'owner'];
    } else if (datasetName.includes('album') || dataObj.type === 'album' || dataObj.album || item.album) {
        type = 'albums';
        displayName = 'Albums';
        searchFields = ['name', 'artists'];
    } else if (datasetName.includes('show') || dataObj.type === 'show' || item.show) {
        type = 'shows';
        displayName = 'Shows/Podcasts';
        searchFields = ['name', 'description', 'publisher'];
    }
    
    // Check for images in various nested structures
    hasImages = !!(dataObj.images && dataObj.images.length > 0) || 
               !!(dataObj.album && dataObj.album.images && dataObj.album.images.length > 0) ||
               !!(dataObj.track && dataObj.track.album && dataObj.track.album.images) ||
               !!(item.album && item.album.images && item.album.images.length > 0) || // Check nested album images
               !!(item.show && item.show.images && item.show.images.length > 0); // Check nested show images
    
    // Extract available fields
    const fields = Object.keys(dataObj);
    
    return { type, displayName, fields, hasImages, searchFields };
}

// Load all spotbak files at startup
loadSpotbakFiles();

// Middleware to pass datasets to all views
app.use((req, res, next) => {
    res.locals.datasets = datasetInfo;
    next();
});

// Routes
app.get('/', (req, res) => {
    res.render('index', {
        datasets: datasetInfo,
        totalItems: Object.values(datasetInfo).reduce((sum, info) => sum + info.count, 0)
    });
});

// Dynamic dataset route
app.get('/dataset/:name', (req, res) => {
    const datasetName = req.params.name;
    const dataset = spotbakData[datasetName];
    const info = datasetInfo[datasetName];
    
    if (!dataset || !info) {
        return res.status(404).render('error', { 
            message: `Dataset '${datasetName}' not found`,
            datasets: datasetInfo 
        });
    }
    
    const page = parseInt(req.query.page) || 1;
    const limit = parseInt(req.query.limit) || getDefaultLimit(info.type);
    const search = req.query.search || '';
    
    let filteredData = Array.isArray(dataset) ? dataset : [dataset];
    
    // Apply search filter
    if (search && info.searchFields.length > 0) {
        filteredData = filteredData.filter(item => {
            // Handle different data structures for search
            let searchObj;
            if (info.type === 'albums' && item.album) {
                searchObj = item.album;
            } else if (info.type === 'shows' && item.show) {
                searchObj = item.show;
            } else if (info.type === 'tracks' && item.track) {
                searchObj = item.track;
            } else {
                searchObj = item.spotify_json || item;
            }
            
            return info.searchFields.some(field => {
                const value = getNestedValue(searchObj, field);
                if (Array.isArray(value)) {
                    return value.some(v => String(v).toLowerCase().includes(search.toLowerCase()));
                }
                return String(value || '').toLowerCase().includes(search.toLowerCase());
            });
        });
    }
    
    const totalPages = Math.ceil(filteredData.length / limit);
    const startIndex = (page - 1) * limit;
    const endIndex = startIndex + limit;
    const paginatedData = filteredData.slice(startIndex, endIndex);
    
    res.render('dataset', {
        datasetName: datasetName,
        info: info,
        data: paginatedData,
        currentPage: page,
        totalPages: totalPages,
        totalItems: filteredData.length,
        search: search,
        limit: limit,
        datasets: datasetInfo
    });
});

// Legacy routes for backward compatibility
app.get('/artists', (req, res) => {
    const artistDataset = findDatasetByType('artists');
    if (artistDataset) {
        return res.redirect(`/dataset/${artistDataset}`);
    }
    res.redirect('/');
});

app.get('/tracks', (req, res) => {
    const trackDataset = findDatasetByType('tracks');
    if (trackDataset) {
        return res.redirect(`/dataset/${trackDataset}`);
    }
    res.redirect('/');
});

app.get('/playlists', (req, res) => {
    const playlistDataset = findDatasetByType('playlists');
    if (playlistDataset) {
        return res.redirect(`/dataset/${playlistDataset}`);
    }
    res.redirect('/');
});

app.get('/albums', (req, res) => {
    const albumDataset = findDatasetByType('albums');
    if (albumDataset) {
        return res.redirect(`/dataset/${albumDataset}`);
    }
    res.redirect('/');
});

// API endpoints for JSON data
app.get('/api/datasets', (req, res) => {
    res.json(datasetInfo);
});

app.get('/api/dataset/:name', (req, res) => {
    const datasetName = req.params.name;
    const dataset = spotbakData[datasetName];
    
    if (!dataset) {
        return res.status(404).json({ error: `Dataset '${datasetName}' not found` });
    }
    
    res.json(dataset);
});

// Legacy API endpoints
app.get('/api/artists', (req, res) => {
    const artistDataset = findDatasetByType('artists');
    if (artistDataset) {
        return res.json(spotbakData[artistDataset]);
    }
    res.json([]);
});

app.get('/api/tracks', (req, res) => {
    const trackDataset = findDatasetByType('tracks');
    if (trackDataset) {
        return res.json(spotbakData[trackDataset]);
    }
    res.json([]);
});

app.get('/api/playlists', (req, res) => {
    const playlistDataset = findDatasetByType('playlists');
    if (playlistDataset) {
        return res.json(spotbakData[playlistDataset]);
    }
    res.json([]);
});

app.get('/api/albums', (req, res) => {
    const albumDataset = findDatasetByType('albums');
    if (albumDataset) {
        return res.json(spotbakData[albumDataset]);
    }
    res.json([]);
});

// Helper functions
function findDatasetByType(type) {
    return Object.keys(datasetInfo).find(key => datasetInfo[key].type === type);
}

function getDefaultLimit(type) {
    switch (type) {
        case 'tracks': return 50;
        case 'artists': return 20;
        case 'playlists': return 12;
        case 'albums': return 16;
        default: return 25;
    }
}

function getNestedValue(obj, path) {
    return path.split('.').reduce((current, key) => {
        if (current && typeof current === 'object') {
            // Handle array access like 'artists[0].name'
            if (key.includes('[') && key.includes(']')) {
                const [arrayKey, indexStr] = key.split('[');
                const index = parseInt(indexStr.replace(']', ''));
                return current[arrayKey] && current[arrayKey][index];
            }
            return current[key];
        }
        return undefined;
    }, obj);
}

// Start server
app.listen(PORT, () => {
    console.log(`🎵 Spotbak Viewer running at http://localhost:${PORT}`);
    const totalItems = Object.values(datasetInfo).reduce((sum, info) => sum + info.count, 0);
    console.log(`📊 Total items loaded: ${totalItems.toLocaleString()} across ${Object.keys(spotbakData).length} datasets`);
});
