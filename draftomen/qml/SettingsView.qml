pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property bool narrow
    required property var displayPreferences
    readonly property int effectiveSystemScalePercent: Math.round(
        root.displayPreferences.applicationFontPixelSize
            / Theme.baseFontPixelSize * 100
    )
    readonly property bool ratingsLoading: root.sessionState
        && root.sessionState.ratings
        && root.sessionState.ratings.phase === "loading"
    readonly property bool ratingsProgressActive: root.ratingsLoading
        && root.sessionState.progress
        && root.sessionState.progress.operation === "ratings"
    readonly property bool ratingsProgressDeterminate: root.activeRatingsProgress
        && root.activeRatingsProgress.total !== null
        && root.activeRatingsProgress.total !== undefined
    readonly property var activeRatingsProgress: root.ratingsProgressActive
        ? root.sessionState.progress : null

    ScrollView {
        id: settingsScroll
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: settingsScroll.availableWidth
            spacing: Theme.gutter

            Label {
                text: "Settings"
                color: Theme.text
                font.pixelSize: Theme.textPixelSize(22)
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: "Draft guidance uses the shared live session. Display choices are saved only for this desktop application."
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: guidanceLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: guidanceLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14

                    Label {
                        text: "DRAFT GUIDANCE"
                        color: Theme.primary
                        font.pixelSize: Theme.textPixelSize(10)
                        font.bold: true
                        font.letterSpacing: 1.1
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Default ranking"; color: Theme.text; font.bold: true }
                            Label {
                                Layout.fillWidth: true
                                text: "Controls recommendation order and backtest comparison."
                                color: Theme.textMuted
                                font.pixelSize: Theme.textPixelSize(11)
                                wrapMode: Text.WordWrap
                            }
                        }
                        DimensionalComboBox {
                            id: defaultRanking
                            objectName: "settingsRankingSelector"
                            Layout.preferredWidth: root.narrow ? 140 : 168
                            model: [
                                { key: "score", label: "DO Score" },
                                { key: "win_rate", label: "17L WR" },
                                { key: "alsa", label: "ALSA" },
                                { key: "mana_value", label: "Mana value" }
                            ]
                            textRole: "label"
                            valueRole: "key"
                            currentIndex: {
                                for (let index = 0; index < model.length; index++)
                                    if (model[index].key === root.sessionState.recommendations.ranking_mode)
                                        return index
                                return 0
                            }
                            Accessible.name: "Default recommendation ranking"
                            Accessible.description: "Changes the shared live-session recommendation ranking."
                            onActivated: sessionProvider.changeRanking(currentValue)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Splash recommendations"; color: Theme.text; font.bold: true }
                            Label {
                                Layout.fillWidth: true
                                text: "Consider supported single-pip cards when fixing allows."
                                color: Theme.textMuted
                                font.pixelSize: Theme.textPixelSize(11)
                                wrapMode: Text.WordWrap
                            }
                        }
                        SettingsSwitch {
                            objectName: "settingsSplashSwitch"
                            checked: root.sessionState.recommendations.splash_enabled
                            Accessible.name: "Splash recommendations"
                            Accessible.description: "Changes the shared live-session splash preference."
                            onToggled: sessionProvider.setSplashEnabled(checked)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: displayLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius

                ColumnLayout {
                    id: displayLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 12
                    Label {
                        text: "DISPLAY"
                        color: Theme.primary
                        font.pixelSize: Theme.textPixelSize(10)
                        font.bold: true
                        font.letterSpacing: 1.1
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Show Backtest navigation"; color: Theme.text; font.bold: true }
                            Label { Layout.fillWidth: true; text: "Show the Backtest button in the navigation rail."; color: Theme.textMuted; font.pixelSize: Theme.textPixelSize(11); wrapMode: Text.WordWrap }
                        }
                        SettingsSwitch {
                            objectName: "settingsShowBacktestSwitch"
                            checked: root.displayPreferences.showBacktest
                            Accessible.name: "Show Backtest navigation"
                            Accessible.description: "Show the Backtest button in the navigation rail."
                            onToggled: root.displayPreferences.setShowBacktest(checked)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Compact density"; color: Theme.text; font.bold: true }
                            Label { Layout.fillWidth: true; text: "Reduce list spacing while retaining 40px targets."; color: Theme.textMuted; font.pixelSize: Theme.textPixelSize(11); wrapMode: Text.WordWrap }
                        }
                        SettingsSwitch {
                            objectName: "settingsCompactDensitySwitch"
                            checked: root.displayPreferences.compactDensity
                            Accessible.name: "Compact density"
                            Accessible.description: "Saved desktop display preference."
                            onToggled: root.displayPreferences.setCompactDensity(checked)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Secondary statistics"; color: Theme.text; font.bold: true }
                            Label { Layout.fillWidth: true; text: "Show ALSA, mana value, and source details."; color: Theme.textMuted; font.pixelSize: Theme.textPixelSize(11); wrapMode: Text.WordWrap }
                        }
                        SettingsSwitch {
                            objectName: "settingsSecondaryStatsSwitch"
                            checked: root.displayPreferences.secondaryStats
                            Accessible.name: "Secondary statistics"
                            Accessible.description: "Saved desktop display preference."
                            onToggled: root.displayPreferences.setSecondaryStats(checked)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Card image preview"; color: Theme.text; font.bold: true }
                            Label { Layout.fillWidth: true; text: "Keep the selected card image visible when space allows."; color: Theme.textMuted; font.pixelSize: Theme.textPixelSize(11); wrapMode: Text.WordWrap }
                        }
                        SettingsSwitch {
                            objectName: "settingsCardPreviewSwitch"
                            checked: root.displayPreferences.cardPreview
                            Accessible.name: "Card image preview"
                            Accessible.description: "Saved desktop display preference."
                            onToggled: root.displayPreferences.setCardPreview(checked)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Detailed build context"; color: Theme.text; font.bold: true }
                            Label { Layout.fillWidth: true; text: "Show pair reasoning and durable build warnings."; color: Theme.textMuted; font.pixelSize: Theme.textPixelSize(11); wrapMode: Text.WordWrap }
                        }
                        SettingsSwitch {
                            objectName: "settingsDetailedBuildContextSwitch"
                            checked: root.displayPreferences.detailedBuildContext
                            Accessible.name: "Detailed build context"
                            Accessible.description: "Saved desktop display preference."
                            onToggled: root.displayPreferences.setDetailedBuildContext(checked)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: accessibilityLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius
                ColumnLayout {
                    id: accessibilityLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8
                    Label { text: "ACCESSIBILITY"; color: Theme.primary; font.pixelSize: Theme.textPixelSize(10); font.bold: true; font.letterSpacing: 1.1 }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: "Follow system text size"; color: Theme.text; font.bold: true }
                            Label {
                                objectName: "settingsSystemTextScalingMessage"
                                Layout.fillWidth: true
                                text: {
                                    const percent = root.effectiveSystemScalePercent
                                    if (!root.displayPreferences.systemTextScaling)
                                        return "Using Draft Omen's 100% baseline. The detected system scale is " + percent + "%."
                                    if (percent === 100)
                                        return "Following system text size. The detected 100% scale matches Draft Omen's default, so no visible size change is expected."
                                    return "Following system text size at the detected " + percent + "% scale."
                                }
                                color: Theme.textMuted
                                wrapMode: Text.WordWrap
                                Accessible.name: text
                                Accessible.description: text
                            }
                        }
                        SettingsSwitch {
                            objectName: "settingsSystemTextScalingSwitch"
                            checked: root.displayPreferences.systemTextScaling
                            Accessible.name: "Follow system text size"
                            Accessible.description: "Use the resolved system text size instead of Draft Omen's 100% baseline."
                            onToggled: root.displayPreferences.setSystemTextScaling(checked)
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: dataLayout.implicitHeight + 32
                color: Theme.surfaceLow
                border.color: Theme.outline
                border.width: 1
                radius: Theme.radius
                ColumnLayout {
                    id: dataLayout
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10
                    Label { text: "DATA STATUS"; color: Theme.primary; font.pixelSize: Theme.textPixelSize(10); font.bold: true; font.letterSpacing: 1.1 }
                    Label {
                        objectName: "settingsCardDataMessage"
                        text: root.sessionState.card_data.message
                        color: Theme.text
                    }
                    Label {
                        objectName: "settingsCardDataLastUpdated"
                        Layout.fillWidth: true
                        text: {
                            const prefix = qsTr("Card metadata updated") + " · "
                            const value = root.sessionState.card_data.last_successful_update
                            if (typeof value !== "string" || value.length === 0)
                                return prefix + qsTr("Never updated")
                            const date = new Date(value)
                            if (isNaN(date.getTime()))
                                return prefix + qsTr("Never updated")
                            return prefix + date.toLocaleString(Qt.locale())
                        }
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                        Accessible.name: text
                        Accessible.description: qsTr("The latest successful card metadata update.")
                    }
                    RowLayout {
                        objectName: "settingsProfileStatusRow"
                        Layout.fillWidth: true
                        Label {
                            text: qsTr("Set profile cache")
                            color: Theme.text
                            font.bold: true
                        }
                        Label {
                            objectName: "settingsProfileCacheStatus"
                            Layout.fillWidth: true
                            text: {
                                const profile = root.sessionState.set_profile
                                if (!profile || !profile.set_code)
                                    return qsTr("Unavailable")
                                const maturity = profile.maturity || "generic"
                                const outcome = profile.refresh_outcome
                                const update = outcome ? " · " + outcome : ""
                                return profile.set_code + " · " + maturity + update
                            }
                            color: Theme.textMuted
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                            Accessible.name: text
                            Accessible.description: qsTr("Read-only set-profile cache and refresh status.")
                        }
                    }
                    Label {
                        objectName: "settingsProfileMessage"
                        Layout.fillWidth: true
                        text: root.sessionState.set_profile
                            ? root.sessionState.set_profile.message
                            : qsTr("Set profile is not configured.")
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                        Accessible.name: text
                        Accessible.description: qsTr("Current set-profile status.")
                    }

                    Label {
                        objectName: "settingsRatingsMessage"
                        text: "Ratings · " + root.sessionState.ratings.message
                        color: Theme.textMuted
                    }
                    Label {
                        objectName: "settingsRatingsLastUpdated"
                        Layout.fillWidth: true
                        text: {
                            const prefix = qsTr("17Lands ratings updated") + " · "
                            const value = root.sessionState.ratings.last_successful_update
                            if (typeof value !== "string" || value.length === 0)
                                return prefix + qsTr("Never updated")
                            const date = new Date(value)
                            if (isNaN(date.getTime()))
                                return prefix + qsTr("Never updated")
                            return prefix + date.toLocaleString(Qt.locale())
                        }
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                        Accessible.name: text
                        Accessible.description: qsTr("The latest successful 17Lands ratings update.")
                    }
                    ColumnLayout {
                        id: ratingsProgressContainer
                        objectName: "settingsRatingsProgressContainer"
                        Layout.fillWidth: true
                        visible: root.ratingsProgressActive
                        spacing: 6

                        Label {
                            objectName: "settingsRatingsProgressMessage"
                            Layout.fillWidth: true
                            text: root.activeRatingsProgress
                                ? root.activeRatingsProgress.message : ""
                            color: Theme.text
                            wrapMode: Text.WordWrap
                            Accessible.name: text
                            Accessible.description: "Current ratings download progress."
                        }

                        ProgressBar {
                            id: ratingsProgressBar
                            objectName: "settingsRatingsProgressBar"
                            Layout.fillWidth: true
                            implicitHeight: 8
                            from: 0
                            to: root.activeRatingsProgress
                                && root.ratingsProgressDeterminate
                                ? root.activeRatingsProgress.total : 1
                            value: root.activeRatingsProgress
                                && root.ratingsProgressDeterminate
                                && root.activeRatingsProgress.completed !== null
                                && root.activeRatingsProgress.completed !== undefined
                                ? root.activeRatingsProgress.completed : 0
                            indeterminate: root.ratingsProgressActive
                                && !root.ratingsProgressDeterminate
                            Accessible.name: root.activeRatingsProgress
                                ? root.activeRatingsProgress.message : ""

                            background: Rectangle {
                                objectName: "settingsRatingsProgressBarBackground"
                                implicitWidth: 280
                                implicitHeight: 8
                                color: Theme.surfaceHigh
                                border.color: Theme.outline
                                border.width: 1
                                radius: 4
                            }

                            contentItem: Item {
                                implicitWidth: 280
                                implicitHeight: 8
                                clip: true

                                Rectangle {
                                    id: ratingsProgressFill
                                    objectName: "settingsRatingsProgressBarFill"
                                    width: root.ratingsProgressDeterminate
                                        ? ratingsProgressBar.visualPosition * parent.width
                                        : parent.width * 0.35
                                    height: parent.height
                                    x: root.ratingsProgressDeterminate ? 0 : -width
                                    color: Theme.primary
                                    radius: 4

                                    SequentialAnimation on x {
                                        running: root.ratingsProgressActive
                                            && ratingsProgressBar.indeterminate
                                        loops: Animation.Infinite
                                        NumberAnimation {
                                            from: -ratingsProgressFill.width
                                            to: ratingsProgressFill.parent.width
                                            duration: 900
                                        }
                                    }
                                }
                            }
                        }
                    }
                    DimensionalButton {
                        id: settingsRatingsDownloadButton
                        objectName: "settingsRatingsDownloadButton"
                        enabled: root.sessionState.ratings.set_code !== null && root.sessionState.ratings.set_code !== undefined && root.sessionState.ratings.phase !== "loading"
                        text: qsTr("Download 17Lands ratings")
                        Accessible.name: qsTr("Download 17Lands ratings")
                        Accessible.description: qsTr("Downloads text-only card performance ratings and color-pair win rates from 17Lands; no card images are downloaded.")
                        onClicked: {
                            ratingsDownloadDialog.returnFocusItem = settingsRatingsDownloadButton
                            ratingsDownloadDialog.open()
                        }
                    }
                }
            }
            Item { Layout.preferredHeight: 12 }
        }
    }

    Dialog {
        id: ratingsDownloadDialog

        property var returnFocusItem: null

        objectName: "settingsRatingsDownloadDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        title: qsTr("Download 17Lands ratings?")
        width: Math.min(420, Math.max(300, parent ? parent.width - 32 : 420))
        x: parent ? Math.max(16, Math.round((parent.width - width) / 2)) : 16
        y: parent ? Math.max(16, Math.round((parent.height - height) / 2)) : 16
        padding: 16

        Overlay.modal: Rectangle {
            color: "#99000000"
        }

        background: Rectangle {
            objectName: "settingsRatingsDownloadDialogBackground"
            color: Theme.surface
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius
        }

        header: Rectangle {
            objectName: "settingsRatingsDownloadDialogHeader"
            implicitHeight: 52
            color: Theme.surfaceHigh
            border.color: Theme.outline
            border.width: 1

            Label {
                objectName: "settingsRatingsDownloadDialogTitle"
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                text: ratingsDownloadDialog.title
                color: Theme.text
                font.pixelSize: Theme.textPixelSize(18)
                font.bold: true
                verticalAlignment: Text.AlignVCenter
                Accessible.name: text
            }
        }

        onClosed: {
            const opener = returnFocusItem
            returnFocusItem = null
            if (opener && opener.visible && opener.enabled)
                opener.forceActiveFocus()
        }

        contentItem: ColumnLayout {
            spacing: 12
            implicitWidth: 360

            Label {
                objectName: "settingsRatingsDownloadDialogMessage"
                Layout.fillWidth: true
                text: qsTr("Download text-only card performance ratings and color-pair win rates from 17Lands for %1? No card images are downloaded.").arg(root.sessionState.ratings.set_code)
                color: Theme.text
                wrapMode: Text.WordWrap
                Accessible.name: text
            }
        }
        footer: DialogButtonBox {
            objectName: "settingsRatingsDownloadDialogFooter"
            implicitHeight: 58
            alignment: Qt.AlignRight
            background: Rectangle {
                objectName: "settingsRatingsDownloadDialogFooterBackground"
                color: Theme.surfaceHigh
                border.color: Theme.outline
                border.width: 1
            }

            DimensionalButton {
                objectName: "settingsRatingsDownloadCancelButton"
                text: "Not now"
                accented: false
                implicitWidth: 96
                Accessible.name: "Cancel ratings download"
                onClicked: ratingsDownloadDialog.close()
            }
            DimensionalButton {
                objectName: "settingsRatingsDownloadConfirmButton"
                text: qsTr("Download 17Lands ratings")
                implicitWidth: 144
                Accessible.name: qsTr("Confirm 17Lands ratings download")
                onClicked: {
                    sessionProvider.requestRatings()
                    ratingsDownloadDialog.close()
                }
            }
        }
    }
}
