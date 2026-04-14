#!/bin/sh
cd "$(dirname "$0")" || exit

node scripts/check-package-installed.js postcss sass autoprefixer || exit

build_style() {
  echo "Creating $1 style..."
  cp resources/vars-$1.scss resources/vars.scss
  npx sass resources:sass_processed
  npx postcss \
      sass_processed/ace-dmoj.css \
      sass_processed/featherlight.css \
      sass_processed/martor-description.css \
      sass_processed/select2-dmoj.css \
      sass_processed/style.css \
      sass_processed/blog-modern.css \
      sass_processed/blog-post.css \
      sass_processed/theme-toggle.css \
      --verbose --use autoprefixer -d "$2"
  rm resources/vars.scss
}

build_style 'default' 'resources'
build_style 'dark' 'resources/dark'

build_style_classic() {
  echo "Creating classic $1 style..."
  cp resources_classic/vars-$1.scss resources_classic/vars.scss
  npx sass resources_classic:sass_processed_classic
  npx postcss \
      sass_processed_classic/ace-dmoj.css \
      sass_processed_classic/featherlight.css \
      sass_processed_classic/martor-description.css \
      sass_processed_classic/select2-dmoj.css \
      sass_processed_classic/style.css \
      sass_processed_classic/blog-modern.css \
      sass_processed_classic/blog-post.css \
      --verbose --use autoprefixer -d "$2"
  rm resources_classic/vars.scss
}

build_style_classic 'default' 'resources/classic'
build_style_classic 'dark' 'resources/classic/dark'
