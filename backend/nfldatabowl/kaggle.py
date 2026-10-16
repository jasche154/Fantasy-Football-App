import kagglehub

# Download latest version
path = kagglehub.competition_download('nfl-big-data-bowl-2027')

print("Path to competition files:", path)