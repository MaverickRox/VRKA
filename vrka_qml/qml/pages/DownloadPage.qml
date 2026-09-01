pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import ".."
import "../components"

ScrollView {
    id: downloadScroll
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    property string errorText: ""

    function currentOptions() {
        var isAudio = modeSegment.selectedIndex === 1;
        var opts = {
            "mode": isAudio ? "audio" : "video",
            "quality": qualityCombo.currentText,
            "fps60": fps60Check.checked,
            "audio_format": audioFormatCombo.currentText,
            "mp3_bitrate": mp3BitrateCombo.currentText,
            "download_subs": subsCheck.checked,
            "sub_langs": Settings.subLangs,
            "embed_subs": Settings.embedSubs,
            "auto_captions": Settings.autoCaptions,
            "embed_thumbnail": Settings.embedThumbnail,
            "embed_metadata": Settings.embedMetadata,
            "sponsorblock": Settings.sponsorblock,
            "output_folder": saveLocationField.text.trim() || Settings.outputFolder,
            "is_playlist": playlistCheck.checked,
            "playlist_start": parseInt(playlistStartField.text.trim()) || 1,
            "playlist_end": parseInt(playlistEndField.text.trim()) || 0,
            "trim_enabled": trimCheck.checked,
            "start_time": trimCheck.checked ? trimStartField.text.trim() : "",
            "end_time": trimCheck.checked ? trimEndField.text.trim() : "",
            "use_custom_command": Settings.useCustomCommand,
            "custom_command": Settings.customCommand
        };
        return opts;
    }

    function submit() {
        if (urlField.text.trim() === "") {
            downloadScroll.errorText = "Please enter a valid media URL to download.";
            return;
        }
        downloadScroll.errorText = "";
        Controller.submitDownload(urlField.text.trim(), downloadScroll.currentOptions());
    }

    function _syncFromSettings() {
        if (qualityCombo.model.indexOf(Settings.quality) !== -1)
            qualityCombo.currentIndex = qualityCombo.model.indexOf(Settings.quality);
        fps60Check.checked = Settings.fps60;
        if (audioFormatCombo.model.indexOf(Settings.audioFormat) !== -1)
            audioFormatCombo.currentIndex = audioFormatCombo.model.indexOf(Settings.audioFormat);
        if (mp3BitrateCombo.model.indexOf(Settings.mp3Bitrate) !== -1)
            mp3BitrateCombo.currentIndex = mp3BitrateCombo.model.indexOf(Settings.mp3Bitrate);
        subsCheck.checked = Settings.downloadSubs;
    }

    Connections {
        target: Controller
        function onSubmissionAccepted(taskId, url) {
            urlField.text = "";
            downloadScroll.errorText = "";
            shell.currentPageIndex = 1;
        }
        function onSubmissionFailed(title, message) {
            downloadScroll.errorText = title + ": " + message;
        }
        function onPrefillRequested(url) {
            urlField.text = url;
            urlField.forceActiveFocus();
        }
    }

    ColumnLayout {
        width: Math.max(100, downloadScroll.availableWidth - 16)
        spacing: downloadScroll.availableHeight > 850 ? 18 : 14

        // Header
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3

            Label {
                text: "Download Media"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.displayTitleSize
                font.bold: true
                color: Theme.text
            }

            Label {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                text: "Capture high-fidelity media streams directly from supported sources."
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodySize
                color: Theme.textDim
                wrapMode: Text.WordWrap
            }
        }

        // Restored 3.0 Top Telemetry & Status Row (Single Coherent Capsule)
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 46
            radius: Theme.cardRadius
            color: Theme.card
            border.width: 1
            border.color: Theme.border

            RowLayout {
                anchors.fill: parent
                spacing: 0

                // 1. QUEUED
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: 10

                        Label {
                            text: String(Bridge.queuedCount).padStart(2, "0")
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.sectionTitleSize
                            font.bold: true
                            color: Bridge.queuedCount > 0 ? Theme.warning : Theme.text
                        }

                        Label {
                            text: "QUEUED"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Bridge.queuedCount > 0 ? Theme.warning : Theme.textDim
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: Theme.hairline
                    Layout.fillHeight: true
                    Layout.topMargin: 8
                    Layout.bottomMargin: 8
                    color: Theme.border
                }

                // 2. ACTIVE
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: 10

                        Label {
                            text: String(Bridge.activeCount).padStart(2, "0")
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.sectionTitleSize
                            font.bold: true
                            color: Bridge.activeCount > 0 ? Theme.accentHover : Theme.text
                        }

                        Label {
                            text: "ACTIVE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Bridge.activeCount > 0 ? Theme.accentHover : Theme.textDim
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: Theme.hairline
                    Layout.fillHeight: true
                    Layout.topMargin: 8
                    Layout.bottomMargin: 8
                    color: Theme.border
                }

                // 3. COMPLETED
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: 10

                        Label {
                            text: String(Bridge.completedCount).padStart(2, "0")
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.sectionTitleSize
                            font.bold: true
                            color: Theme.success
                        }

                        Label {
                            text: "COMPLETED"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.success
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: Theme.hairline
                    Layout.fillHeight: true
                    Layout.topMargin: 8
                    Layout.bottomMargin: 8
                    color: Theme.border
                }

                // 4. ARCHIVED
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: 10

                        Label {
                            text: String(Bridge.historyCount).padStart(2, "0")
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.sectionTitleSize
                            font.bold: true
                            color: Theme.text
                        }

                        Label {
                            text: "ARCHIVED"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.warning
                        }
                    }
                }
            }
        }

        // Section 1: Media Source Composer
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: sourceCol.implicitHeight + 28
            radius: Theme.cardRadius
            color: Theme.card
            border.width: 1
            border.color: Theme.border

            ColumnLayout {
                id: sourceCol
                anchors.fill: parent
                anchors.margins: 14
                spacing: 12

                // Section Label with link icon
                RowLayout {
                    spacing: 8
                    Image {
                        source: Qt.resolvedUrl("../../../assets/branding/v2icons/link-accent-32.png")
                        Layout.preferredWidth: 14
                        Layout.preferredHeight: 14
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }
                    Label {
                        text: "MEDIA SOURCE & DESTINATION"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }
                }

                // Hero URL Input with Integrated Paste Pill
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 46
                    radius: Theme.controlRadius
                    color: Theme.cardAlt
                    border.width: urlInputInner.activeFocus ? 2 : 1
                    border.color: urlInputInner.activeFocus ? Theme.accent : Theme.borderStrong

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 6
                        spacing: 8

                        TextInput {
                            id: urlInputInner
                            Layout.fillWidth: true
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.bodySize
                            color: Theme.text
                            selectByMouse: true
                            clip: true

                            Text {
                                anchors.fill: parent
                                text: "https://..."
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.bodySize
                                color: Theme.textDisabled
                                visible: !urlInputInner.text && !urlInputInner.activeFocus
                                verticalAlignment: Text.AlignVCenter
                            }

                            Keys.onReturnPressed: downloadScroll.submit()
                        }

                        VPrimaryButton {
                            text: "Paste"
                            Layout.preferredHeight: 34
                            Layout.preferredWidth: 72
                            onClicked: {
                                var clip = Controller.getClipboardText();
                                if (clip) {
                                    urlInputInner.text = clip.trim();
                                }
                            }
                        }
                    }
                }

                // Save Location Row
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Label {
                        text: "Save Location:"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.smallSize
                        font.bold: true
                        color: Theme.text
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        implicitHeight: 36
                        radius: Theme.controlRadius
                        color: Theme.cardAlt
                        border.width: 1
                        border.color: Theme.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12

                            Label {
                                id: saveLocationLabel
                                Layout.fillWidth: true
                                Layout.preferredWidth: 0
                                text: Settings.outputFolder || "Default folder"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.smallSize
                                color: Theme.textMuted
                                elide: Text.ElideMiddle
                            }
                        }
                    }

                    VSecondaryButton {
                        text: "Browse"
                        Layout.preferredHeight: 36
                        onClicked: folderDialog.open()
                    }
                }
            }
        }

        // Section 2: Output Configuration
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: configCol.implicitHeight + 28
            radius: Theme.cardRadius
            color: Theme.card
            border.width: 1
            border.color: Theme.border

            ColumnLayout {
                id: configCol
                anchors.fill: parent
                anchors.margins: 14
                spacing: 12

                RowLayout {
                    spacing: 8
                    Image {
                        source: Qt.resolvedUrl("../../../assets/branding/v2icons/gear-accent-32.png")
                        Layout.preferredWidth: 14
                        Layout.preferredHeight: 14
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }
                    Label {
                        text: "OUTPUT CONFIGURATION"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }
                }

                // Segmented Stream Switch
                VSegmentedButton {
                    id: modeSegment
                    options: ["Video Stream", "Audio Only"]
                    selectedIndex: 0
                }

                // Video Options Responsive 2-Column Grid (NO Orphaned Checkbox)
                GridLayout {
                    visible: modeSegment.selectedIndex === 0
                    Layout.fillWidth: true
                    columns: downloadScroll.availableWidth > 680 ? 2 : 1
                    columnSpacing: 20
                    rowSpacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "TARGET QUALITY"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VComboBox {
                            id: qualityCombo
                            Layout.fillWidth: true
                            model: ["Best Available", "8K (4320p)", "4K (2160p)", "2K (1440p)", "1080p Full HD", "720p HD", "480p SD", "360p Low", "Worst Available"]
                            currentIndex: 0
                        }
                    }

                    VCheckBox {
                        id: fps60Check
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignBottom
                        Layout.bottomMargin: 6
                        text: "Prefer 60 FPS when stream available"
                        checked: true
                    }
                }

                // Audio Options Responsive 2-Column Grid
                GridLayout {
                    visible: modeSegment.selectedIndex === 1
                    Layout.fillWidth: true
                    columns: downloadScroll.availableWidth > 680 ? 2 : 1
                    columnSpacing: 20
                    rowSpacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "AUDIO CONTAINER FORMAT"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VComboBox {
                            id: audioFormatCombo
                            Layout.fillWidth: true
                            model: ["MP3 (Broad Compatibility)", "M4A / AAC (High Efficiency)", "FLAC (Lossless Audio)", "OPUS (Modern Codec)", "WAV (Uncompressed)", "OGG Vorbis"]
                            currentIndex: 0
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "TARGET BITRATE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VComboBox {
                            id: mp3BitrateCombo
                            Layout.fillWidth: true
                            model: ["320 kbps (Extreme Quality)", "256 kbps (High Quality)", "192 kbps (Standard)", "128 kbps (Compact)", "V0 (Variable Extreme)"]
                            currentIndex: 0
                        }
                    }
                }
            }
        }

        // Section 3 & 4: Secondary Configuration 2-Column Grid
        GridLayout {
            Layout.fillWidth: true
            columns: downloadScroll.availableWidth > 780 ? 2 : 1
            columnSpacing: 14
            rowSpacing: 14

            // Playlist Scope Card
            VCard {
                Layout.fillWidth: true
                headerTitle: "Playlist Scope"
                headerSubtitle: "Sequential range extraction"
                headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/list-accent-32.png")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    VCheckBox {
                        id: playlistCheck
                        Layout.fillWidth: true
                        text: "Download entire playlist"
                        checked: false
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            spacing: 4

                            Label {
                                text: "START INDEX"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }

                            VTextField {
                                id: playlistStartField
                                Layout.fillWidth: true
                                text: "1"
                                placeholderText: "1"
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            spacing: 4

                            Label {
                                text: "END INDEX"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }

                            VTextField {
                                id: playlistEndField
                                Layout.fillWidth: true
                                text: "last"
                                placeholderText: "last"
                            }
                        }
                    }
                }
            }

            // Subtitles & Trim Card
            VCard {
                Layout.fillWidth: true
                headerTitle: "Subtitles & Trim"
                headerSubtitle: "Text tracks & temporal slicing"
                headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/subtitles-accent-32.png")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        VCheckBox {
                            id: subsCheck
                            Layout.fillWidth: true
                            text: "Download subtitles"
                            checked: false
                        }

                        VCheckBox {
                            id: trimCheck
                            Layout.fillWidth: true
                            text: "Enable trim range"
                            checked: false
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            spacing: 4

                            Label {
                                text: "START (HH:MM:SS)"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }

                            VTextField {
                                id: trimStartField
                                Layout.fillWidth: true
                                text: "00:00:00"
                                placeholderText: "00:00:00"
                                enabled: trimCheck.checked
                                opacity: trimCheck.checked ? 1.0 : 0.45
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            spacing: 4

                            Label {
                                text: "END (HH:MM:SS)"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }

                            VTextField {
                                id: trimEndField
                                Layout.fillWidth: true
                                text: "00:00:00"
                                placeholderText: "00:00:00"
                                enabled: trimCheck.checked
                                opacity: trimCheck.checked ? 1.0 : 0.45
                            }
                        }
                    }
                }
            }
        }

        // Section 5: Bottom Action Row
        RowLayout {
            Layout.fillWidth: true
            spacing: 14

            Rectangle {
                visible: downloadScroll.errorText !== ""
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                implicitHeight: 40
                radius: Theme.controlRadius
                color: Theme.errorSoft
                border.width: 1
                border.color: Theme.error

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12

                    Label {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        text: downloadScroll.errorText
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.smallSize
                        font.bold: true
                        color: Theme.error
                        elide: Text.ElideRight
                    }
                }
            }

            Item {
                visible: downloadScroll.errorText === ""
                Layout.fillWidth: true
            }

            VPrimaryButton {
                id: submitBtn
                text: "Add to Queue →"
                Layout.preferredHeight: 40
                Layout.preferredWidth: 180
                onClicked: downloadScroll.submit()
            }
        }

        Item { Layout.preferredHeight: 16 }
    }

    FolderDialog {
        id: folderDialog
        title: "Select Save Folder"
        onAccepted: {
            var path = selectedFolder.toString();
            if (path.startsWith("file:///")) {
                path = path.substring(8);
            }
            Settings.outputFolder = path;
        }
    }

    Component.onCompleted: {
        _syncFromSettings();
    }

    property alias urlField: urlInputInner
    property alias saveLocationField: saveLocationLabel
}
