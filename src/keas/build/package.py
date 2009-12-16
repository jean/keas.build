##############################################################################
#
# Copyright (c) 2008 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Package Builder

$Id$
"""
__docformat__ = 'ReStructuredText'
import BeautifulSoup
import ConfigParser
import base64
import logging
import optparse
import os
import pkg_resources
import re
import shutil
import sys
import stat
import tempfile
import urllib
import urllib2
from xml.etree import ElementTree
from keas.build import base

logger = base.logger

is_win32 = sys.platform == 'win32'

def checkRO(function, path, excinfo):
    if (function == os.remove
        and excinfo[0] == WindowsError
        and excinfo[1].winerror == 5):
        #Access is denied
        #because it's a readonly file
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)

def rmtree(dirname):
    if is_win32:
        shutil.rmtree(dirname, ignore_errors=False, onerror=checkRO)
    else:
        shutil.rmtree(dirname)

class PackageBuilder(object):

    pkg = None
    customPath = None
    options = None

    uploadType = 'internal'
    packageIndexUrl = None
    packageIndexUsername = None
    packageIndexPassword = None

    svnRepositoryUrl = None

    tagLayout = 'flat'
    svn = None

    def __init__(self, pkg, options):
        self.pkg = pkg
        self.options = options

    def getTagURL(self, version):
        reposUrl = self.svnRepositoryUrl
        separator = '-'
        if self.tagLayout == 'flat':
            separator = '-'
        elif self.tagLayout == 'subfolder':
            separator = '/'
        if self.customPath:
            reposUrl = urllib.basejoin(reposUrl, self.customPath)
            tagUrl = reposUrl.split('%s')[0] + 'tags/%s%s%s' %(
                self.pkg, separator, version)
        else:
            tagUrl = reposUrl + 'tags/%s%s%s' %(self.pkg, separator, version)
        logger.debug('Tag URL: ' + tagUrl)
        return tagUrl

    def getBranchURL(self, branch):
        reposUrl = self.svnRepositoryUrl
        if self.customPath:
            reposUrl = urllib.basejoin(reposUrl, self.customPath)
            branchUrl = reposUrl %('branches/' + branch)
            if branch == 'trunk':
                branchUrl = reposUrl %branch
        else:
            branchUrl = reposUrl + 'branches/' + branch + '/' + self.pkg + '/'
            if branch == 'trunk':
                branchUrl = reposUrl + 'trunk/' + self.pkg
        logger.debug('Branch URL: ' + branchUrl)
        return branchUrl

    def getRevision(self, url):
        #xml = base.do('svn info --xml ' + url)
        xml = self.svn.info(url)
        elem = ElementTree.fromstring(xml)
        revision = elem.find("entry").find("commit").get("revision")
        if not revision:
            revision = 0
        else:
            revision = int(revision)
        logger.debug('Revision for %s: %i' %(url, revision))
        return revision

    def findVersions(self):
        if self.options.offline:
            logger.info('Offline: Skip looking for versions.')
            return []

        logger.debug('Package Index: ' + self.packageIndexUrl)
        req = urllib2.Request(self.packageIndexUrl)

        if self.packageIndexUsername:
            base64string = base64.encodestring(
                '%s:%s' % (self.packageIndexUsername,
                           self.packageIndexPassword))[:-1]
            req.add_header("Authorization", "Basic %s" % base64string)

        soup = BeautifulSoup.BeautifulSoup(urllib2.urlopen(req).read())

        VERSION = re.compile(self.pkg+r'-(\d+\.\d+(\.\d+){0,2})')

        simplePageUrl = None

        versions = []
        for tag in soup('a'):
            cntnt = str(tag.contents[0]) # str: re does not like non-strings

            if cntnt == self.pkg:
                try:
                    simplePageUrl = tag['href']
                except KeyError:
                    pass

            m = VERSION.search(cntnt)
            if m:
                versions.append(m.group(1))

        if len(versions) == 0 and simplePageUrl:
            #we probably hit a PYPI-like simple index
            #reload the linked page, check again for versions
            req = urllib2.Request(simplePageUrl)

            if self.packageIndexUsername:
                base64string = base64.encodestring(
                    '%s:%s' % (self.packageIndexUsername,
                               self.packageIndexPassword))[:-1]
                req.add_header("Authorization", "Basic %s" % base64string)

            soup = BeautifulSoup.BeautifulSoup(urllib2.urlopen(req).read())

            for tag in soup('a'):
                cntnt = str(tag.contents[0]) # str: re does not like non-strings

                m = VERSION.search(cntnt)
                if m:
                    versions.append(m.group(1))

        logger.debug('All versions: ' + ' '.join(versions))

        # filter versions by ones that came from the branch we are building from.
        if self.options.branch and '-' in self.options.branch:
            branchVersion = self.options.branch.split('-')[-1]
            branchVersionParts = tuple([p for p in
                                        pkg_resources.parse_version(branchVersion)
                                        if not p.startswith('*')])
            def fromBranch(v):
                versionParts = pkg_resources.parse_version(v)
                return versionParts[:len(branchVersionParts)] == branchVersionParts
            versions = filter(fromBranch, versions)
        return sorted(versions, key=lambda x: pkg_resources.parse_version(x))

    def getBranches(self):
        if self.options.offline:
            logger.info('Offline: Skip looking for branches.')
            return []
        url = self.svnRepositoryUrl
        if self.customPath:
            url = urllib.basejoin(url, self.customPath.split('%s')[0])
        url += 'branches'
        logger.debug('Branches URL: ' + url)

        #xml = base.do('svn ls --xml ' + url)
        xml = self.svn.ls(url)
        elem = ElementTree.fromstring(xml)

        branches = [elem.text for elem in elem.findall('./list/entry/name')]

        #if self.svnRepositoryUsername:
        #    base64string = base64.encodestring('%s:%s' % (
        #        self.svnRepositoryUsername, self.svnRepositoryPassword))[:-1]
        #    req.add_header("Authorization", "Basic %s" % base64string)
        #
        #soup = BeautifulSoup.BeautifulSoup(urllib2.urlopen(req).read())
        #branches  = [tag.contents[0][:-1]
        #             for tag in soup('ul')[0]('a')
        #             if tag.contents[0] != '..']
        logger.debug('Branches: ' + ' '.join(branches))
        return branches

    def hasChangedSince(self, version, branch):
        # setup.py gets changed on the branch after the tag is created, so
        # that the branch always has a later revision. So let's check the
        # source directory instead.
        branchUrl = self.getBranchURL(branch) + '/src'
        tagUrl = self.getTagURL(version)
        changed = self.getRevision(branchUrl) > self.getRevision(tagUrl)
        if changed:
            logger.info(
                'Branch %r changed since the release of version %s' %(
                branch, version))
        return changed

    def createRelease(self, version, branch):
        logger.info('Creating release %r for %r from branch %r' %(
            version, self.pkg, branch))
        # 0. Skip creating releases in offline mode.
        if self.options.offline:
            logger.info('Offline: Skip creating a release.')
            return
        # 1. Create Release Tag
        branchUrl = self.getBranchURL(branch)
        tagUrl = self.getTagURL(version)

        logger.info('Creating release tag')
        #TODO: destination folder might not exist... create it
        self.svn.cp(branchUrl, tagUrl, "Create release tag %s." % version)
        #base.do('svn cp -m "Create release tag %s." %s %s' %(
        #    version, branchUrl, tagUrl))

        # 2. Download tag
        buildDir = tempfile.mkdtemp()
        tagDir = os.path.join(buildDir, '%s-%s' %(self.pkg, version))
        self.svn.co(tagUrl, tagDir)
        #base.do('svn co %s %s' %(tagUrl, tagDir))

        # 3. Create release
        # 3.1. Remove setup.cfg
        logger.info("Updating tag version metadata")
        setupCfgPath = os.path.join(tagDir, 'setup.cfg')
        if os.path.exists(setupCfgPath):
            os.remove(setupCfgPath)
        # 3.2. Update the version
        setuppy = file(os.path.join(tagDir, 'setup.py'), 'r').read()
        setuppy = re.sub(
            "version ?= ?'(.*)',", "version = '%s'," %version, setuppy)
        file(os.path.join(tagDir, 'setup.py'), 'w').write(setuppy)
        # 3.3. Check it all in
        self.svn.ci(tagDir, "Prepare for release %s." % version)
        #base.do('svn ci -m "Prepare for release %s." %s' %(version, tagDir))

        # 4. Upload the distribution
        if self.uploadType == 'internal':
            # 3.4. Create distribution
            logger.info("Creating release tarball")
            base.do('python setup.py sdist', cwd = tagDir)

            if is_win32:
                ext = 'zip'
            else:
                ext = 'tar.gz'
            distributionFileName = os.path.join(
                tagDir, 'dist', '%s-%s.%s' %(self.pkg, version, ext))
            if not self.options.noUpload:
                logger.info("Uploading release.")
                base.uploadFile(
                    distributionFileName,
                    self.packageIndexUrl,
                    self.packageIndexUsername, self.packageIndexPassword,
                    self.options.offline)
        elif self.uploadType == 'setup.py':
            # 3.4. Create distribution and upload in one step
            logger.info("Uploading release to PyPI.")
            base.do('python setup.py sdist register upload', cwd = tagDir)
        else:
            logger.warn('Unknown uploadType: ' + self.uploadType)

        # 5. Update the start branch to the next devel version
        if not self.options.noBranchUpdate:
            logger.info("Updating branch version metadata")
            # 5.1. Check out the branch.
            branchDir = os.path.join(buildDir, 'branch')
            self.svn.co(branchUrl, branchDir)
            #base.do('svn co --non-recursive %s %s' %(branchUrl, branchDir))
            # 5.2. Get the current version.
            setuppy = file(os.path.join(branchDir, 'setup.py'), 'r').read()
            currVersion = re.search("version ?= ?'(.*)',", setuppy)
            if not currVersion:
                logger.error("No version =  found in setup.py, cannot update!")
            else:
                currVersion = currVersion.groups()[0]
                # 5.3. Update setup/py to the next version of the currently
                #      released one
                newVersion = base.guessNextVersion(version) + 'dev'
                setuppy = re.sub(
                    "version ?= ?'(.*)',", "version = '%s'," %newVersion, setuppy)
                file(os.path.join(branchDir, 'setup.py'), 'w').write(setuppy)
                # 5.4. Check in the changes.
                self.svn.ci(branchDir, "Update version number to %s." % newVersion)
                #base.do('svn ci -m "Update version number to %s." %s' %(
                #    newVersion, branchDir))

        # 6. Cleanup
        rmtree(buildDir)

    def runCLI(self, configFile, askToCreateRelease=False, forceSvnAuth=False):
        logger.info('Start releasing new version of ' + self.pkg)
        # 1. Read the configuration file.
        logger.info('Loading configuration file: ' + configFile)
        config = ConfigParser.RawConfigParser()
        config.read(configFile)
        # 1.1. Get package index info.
        self.packageIndexUrl = config.get(
            base.BUILD_SECTION, 'package-index')
        self.packageIndexUsername = config.get(
            base.BUILD_SECTION, 'package-index-username')
        self.packageIndexPassword = config.get(
            base.BUILD_SECTION, 'package-index-password')
        # 1.2. Get svn repository info.
        self.svnRepositoryUrl = config.get(
            base.BUILD_SECTION, 'svn-repos')

        if forceSvnAuth:
            svnRepositoryUsername = config.get(
                base.BUILD_SECTION, 'svn-repos-username')
            svnRepositoryPassword = config.get(
                base.BUILD_SECTION, 'svn-repos-password')

            self.svn = base.SVN(svnRepositoryUsername, svnRepositoryPassword,
                                forceAuth=True)
        else:
            self.svn = base.SVN()

        try:
            self.uploadType = config.get(
                base.BUILD_SECTION, 'upload-type')
        except ConfigParser.NoOptionError:
            self.uploadType = 'internal'

        try:
            self.tagLayout = config.get(
                base.BUILD_SECTION, 'tag-layout')
        except ConfigParser.NoOptionError:
            self.tagLayout = 'flat'

        # 1.3. Determine the possibly custom path.
        for pkg in config.get(base.BUILD_SECTION, 'packages').split():
            if pkg.startswith(self.pkg):
                if ':' in pkg:
                    self.customPath = pkg.split(':')[1]
                break

        # 2. Find all versions.
        versions = self.findVersions()
        logger.info('Existing %s versions: %s' % (
            self.pkg, ' | '.join(reversed(versions))))

        # 3. Determine the default version to suggest.
        defaultVersion = None

        # 3.1 Set default version based on forceVersion
        forceVersion = self.options.forceVersion
        if forceVersion:
            if forceVersion in versions:
                logger.error('Forced version %s already exists' % forceVersion)
            else:
                defaultVersion = forceVersion

        if versions and not defaultVersion:
            # 3.2. If the branch was specified, check whether it changed since
            # the last release.
            changed = False
            if self.options.branch:
                logger.info('Checking for changes since version %s; please wait...', versions[-1])
                changed = self.hasChangedSince(
                    versions[-1], self.options.branch)
                if not changed:
                    logger.info("No changes detected.")
            else:
                logger.info("Not checking for changes since version %s because no branch was specified.", versions[-1])
            # 3.3. If the branch changed and the next version should be
            # suggested, let's find the next version.
            if self.options.nextVersion and changed:
                defaultVersion = base.guessNextVersion(versions[-1])
            else:
                defaultVersion = versions[-1]
        while True:
            version = base.getInput(
                'Version for `%s`' %self.pkg, defaultVersion,
                self.options.useDefaults and defaultVersion is not None)
            if version not in versions and not self.options.offline:
                if askToCreateRelease:
                    print 'The release %s-%s does not exist.' %(pkg, version)
                    doRelease = base.getInput(
                        'Do you want to create it? yes/no', 'yes',
                        self.options.useDefaults)
                    if doRelease == 'no':
                        continue
                # 4. Now create a release for this version.
                if not self.options.offline:
                    # 4.1. Determine the branch from which to base the release
                    # on.
                    branch = self.options.branch
                    if branch is None:
                        print 'Available Branches:'
                        for branch in self.getBranches():
                            print '  * ' + branch
                        print '  * trunk'
                        branch = base.getInput(
                            'What branch do you want to use?', 'trunk',
                            self.options.useDefaults)
                    # 4.2. Create the release.
                    self.createRelease(version, branch)
            break
        # 5. Return the version number.
        logger.info('Chosen version: ' + version)
        return version


def main(args=None):
    # Make sure we get the arguments.
    if args is None:
        args = sys.argv[1:]
    if not args:
        args = ['-h']

    # Set up logger handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(base.formatter)
    logger.addHandler(handler)

    # Parse arguments
    options, args = base.parser.parse_args(args)

    logger.setLevel(logging.INFO)
    if options.verbose:
        logger.setLevel(logging.DEBUG)
    if options.quiet:
        logger.setLevel(logging.FATAL)

    if len(args) == 0:
        print "No package was specified."
        print "Usage: build-package [options] package1 package2 ..."
        sys.exit(0)
    for pkg in args:
        builder = PackageBuilder(pkg, options,
                                 forceSvnAuth = options.forceSvnAuth)
        try:
            builder.runCLI(options.configFile)
        except KeyboardInterrupt:
            logger.info("Quitting")
            sys.exit(0)

    # Remove the handler again.
    logger.removeHandler(handler)

    # Exit cleanly.
    sys.exit(0)
