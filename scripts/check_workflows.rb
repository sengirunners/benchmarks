#!/usr/bin/env ruby
# frozen_string_literal: true

# Fails if any workflow in .github/workflows could be started by anyone but a maintainer or the
# schedule, could write to the repository from a measuring job, reads a secret, or uses an action
# that is not pinned to a full commit SHA. The collect job runs this before it collects.
#
#   ruby scripts/check_workflows.rb
require "yaml"

ROOT = File.expand_path("..", __dir__)
ALLOWED_TRIGGERS = %w[workflow_dispatch schedule].freeze
# The only job that may write: it commits results/ and runs on GitHub's own runner.
WRITERS = {"collect.yml" => {"collect" => "ubuntu-latest"}}.freeze
SHA_PIN = %r{\A[\w.-]+/[\w./-]+@[0-9a-f]{40}\z}
# A project's own composite actions, inside the project checkout (none live in this repository).
PROJECT_LOCAL = %r{\A\./\.github/actions/[\w-]+\z}

errors = []
files = Dir[File.join(ROOT, ".github/workflows/*")].sort
errors << "no workflow files found" if files.empty?

files.each do |path|
  name = File.basename(path)
  unless name.end_with?(".yml", ".yaml")
    errors << "#{name}: not a .yml workflow file"
    next
  end
  text = File.read(path)
  errors << "#{name}: reads a secret" if text.match?(/\bsecrets\s*[.\[]/)
  errors << "#{name}: uses pull_request_target" if text.include?("pull_request_target")
  doc = YAML.safe_load(text, aliases: false)
  # YAML 1.1 reads a bare `on` key as true.
  on = doc.key?("on") ? doc["on"] : doc[true]
  triggers = case on
  when String then [on]
  when Array then on
  when Hash then on.keys
  else []
  end
  errors << "#{name}: no triggers" if triggers.empty?
  (triggers - ALLOWED_TRIGGERS).each { errors << "#{name}: trigger #{it} is not allowed (only #{ALLOWED_TRIGGERS.join(", ")})" }

  top = doc["permissions"]
  errors << "#{name}: top-level permissions must be exactly contents: read" unless top == {"contents" => "read"}

  (doc["jobs"] || {}).each do |id, job|
    perms = job["permissions"]
    writer = WRITERS.dig(name, id)
    if writer
      errors << "#{name}/#{id}: the writing job must run on #{writer}" unless job["runs-on"] == writer
      unless perms.is_a?(Hash) && perms.values.all? { %w[read write].include?(it) } && (perms.keys - %w[contents actions]).empty?
        errors << "#{name}/#{id}: permissions may only be contents and actions"
      end
    else
      errors << "#{name}/#{id}: permissions must be exactly contents: read" unless perms == {"contents" => "read"}
    end
    errors << "#{name}/#{id}: calls a reusable workflow" if job["uses"]
    (job["steps"] || []).each do |step|
      uses = step["uses"] or next
      next if uses.match?(SHA_PIN) || uses.match?(PROJECT_LOCAL)
      errors << "#{name}/#{id}: action #{uses} is not pinned to a full commit SHA"
    end
  end
end

if errors.empty?
  puts "ok: #{files.size} workflow files, triggers #{ALLOWED_TRIGGERS.join(" and ")} only, no secrets, actions pinned"
else
  errors.each { warn "error: #{it}" }
  exit 1
end
